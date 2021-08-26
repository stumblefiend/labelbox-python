from labelbox import utils
import os
import json
import logging
from itertools import islice
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import ndjson
from io import StringIO
import requests

from labelbox.exceptions import InvalidQueryError, LabelboxError, ResourceNotFoundError, InvalidAttributeError
from labelbox.orm.db_object import DbObject, Updateable, Deletable
from labelbox.orm.model import Entity, Field, Relationship

logger = logging.getLogger(__name__)


class Dataset(DbObject, Updateable, Deletable):
    """ A Dataset is a collection of DataRows.

    Attributes:
        name (str)
        description (str)
        updated_at (datetime)
        created_at (datetime)
        row_count (int): The number of rows in the dataset. Fetch the dataset again to update since this is cached.

        projects (Relationship): `ToMany` relationship to Project
        data_rows (Relationship): `ToMany` relationship to DataRow
        created_by (Relationship): `ToOne` relationship to User
        organization (Relationship): `ToOne` relationship to Organization

    """
    name = Field.String("name")
    description = Field.String("description")
    updated_at = Field.DateTime("updated_at")
    created_at = Field.DateTime("created_at")
    row_count = Field.Int("row_count")

    # Relationships
    projects = Relationship.ToMany("Project", True)
    data_rows = Relationship.ToMany("DataRow", False)
    created_by = Relationship.ToOne("User", False, "created_by")
    organization = Relationship.ToOne("Organization", False)

    def create_data_row(self, **kwargs):
        """ Creates a single DataRow belonging to this dataset.

        >>> dataset.create_data_row(row_data="http://my_site.com/photos/img_01.jpg")

        Args:
            **kwargs: Key-value arguments containing new `DataRow` data. At a minimum,
                must contain `row_data`.

        Raises:
            InvalidQueryError: If `DataRow.row_data` field value is not provided
                in `kwargs`.
            InvalidAttributeError: in case the DB object type does not contain
                any of the field names given in `kwargs`.

        """
        DataRow = Entity.DataRow
        if DataRow.row_data.name not in kwargs:
            raise InvalidQueryError(
                "DataRow.row_data missing when creating DataRow.")

        # If row data is a local file path, upload it to server.
        row_data = kwargs[DataRow.row_data.name]
        if os.path.exists(row_data):
            kwargs[DataRow.row_data.name] = self.client.upload_file(row_data)

        kwargs[DataRow.dataset.name] = self

        return self.client._create(DataRow, kwargs)

    def create_data_rows(self, items):
        """ Creates multiple DataRow objects based on the given `items`.

        Each element in `items` can be either a `str` or a `dict`. If
        it is a `str`, then it is interpreted as a local file path. The file
        is uploaded to Labelbox and a DataRow referencing it is created.

        If an item is a `dict`, then it could support one of the two following structures
            1. For static imagery, video, and text it should map `DataRow` field names to values.
               At the minimum an `item` passed as a `dict` must contain a `row_data` key and value.
               If the value for row_data is a local file path and the path exists,
               then the local file will be uploaded to labelbox.

            2. For tiled imagery the dict must match the import structure specified in the link below
               https://docs.labelbox.com/data-model/en/index-en#tiled-imagery-import

        >>> dataset.create_data_rows([
        >>>     {DataRow.row_data:"http://my_site.com/photos/img_01.jpg"},
        >>>     {DataRow.row_data:"/path/to/file1.jpg"},
        >>>     "path/to/file2.jpg",
        >>>     {"tileLayerUrl" : "http://", ...}
        >>>     ])

        For an example showing how to upload tiled data_rows see the following notebook:
            https://github.com/Labelbox/labelbox-python/blob/ms/develop/model_assisted_labeling/tiled_imagery_mal.ipynb

        Args:
            items (iterable of (dict or str)): See above for details.

        Returns:
            Task representing the data import on the server side. The Task
            can be used for inspecting task progress and waiting until it's done.

        Raises:
            InvalidQueryError: If the `items` parameter does not conform to
                the specification above or if the server did not accept the
                DataRow creation request (unknown reason).
            ResourceNotFoundError: If unable to retrieve the Task for the
                import process. This could imply that the import failed.
            InvalidAttributeError: If there are fields in `items` not valid for
                a DataRow.
        """
        file_upload_thread_count = 20
        DataRow = Entity.DataRow

        def upload_if_necessary(item):
            row_data = item['row_data']
            if os.path.exists(row_data):
                item_url = self.client.upload_file(item['row_data'])
                item = {
                    "row_data": item_url,
                    "external_id": item.get('external_id', item['row_data']),
                    "attachments": item.get('attachments', [])
                }
            return item

        def validate_attachments(item):
            attachments = item.get('attachments')
            if attachments:
                if isinstance(attachments, list):
                    for attachment in attachments:
                        Entity.AssetAttachment.validate_attachment_json(
                            attachment)
                else:
                    raise ValueError(
                        f"Attachments must be a list. Found {type(attachments)}"
                    )
            return attachments

        def format_row(item):
            # Formats user input into a consistent dict structure
            if isinstance(item, dict):
                # Convert fields to strings
                item = {
                    key.name if isinstance(key, Field) else key: value
                    for key, value in item.items()
                }
            elif isinstance(item, str):
                # The main advantage of using a string over a dict is that the user is specifying
                # that the file should exist locally.
                # That info is lost after this section so we should check for it here.
                if not os.path.exists(item):
                    raise ValueError(f"Filepath {item} does not exist.")
                item = {"row_data": item, "external_id": item}
            return item

        def validate_keys(item):
            if 'row_data' not in item:
                raise InvalidQueryError(
                    "`row_data` missing when creating DataRow.")

            invalid_keys = set(item) - {
                *{f.name for f in DataRow.fields()}, 'attachments'
            }
            if invalid_keys:
                raise InvalidAttributeError(DataRow, invalid_keys)
            return item

        def convert_item(item):
            # Don't make any changes to tms data
            if "tileLayerUrl" in item:
                validate_attachments(item)
                return item
            # Convert all payload variations into the same dict format
            item = format_row(item)
            # Make sure required keys exist (and there are no extra keys)
            validate_keys(item)
            # Make sure attachments are valid
            validate_attachments(item)
            # Upload any local file paths
            item = upload_if_necessary(item)

            return {
                "data" if key == "row_data" else utils.camel_case(key): value
                for key, value in item.items()
            }

        if not isinstance(items, list):
            raise ValueError(
                f"Must pass a list to create_data_rows. Found {type(items)}")

        with ThreadPoolExecutor(file_upload_thread_count) as executor:
            futures = [executor.submit(convert_item, item) for item in items]
            items = [future.result() for future in as_completed(futures)]

        # Prepare and upload the desciptor file
        data = json.dumps(items)
        descriptor_url = self.client.upload_data(data)
        # Create data source
        dataset_param = "datasetId"
        url_param = "jsonUrl"
        query_str = """mutation AppendRowsToDatasetPyApi($%s: ID!, $%s: String!){
            appendRowsToDataset(data:{datasetId: $%s, jsonFileUrl: $%s}
            ){ taskId accepted errorMessage } } """ % (dataset_param, url_param,
                                                       dataset_param, url_param)

        res = self.client.execute(query_str, {
            dataset_param: self.uid,
            url_param: descriptor_url
        })
        res = res["appendRowsToDataset"]
        if not res["accepted"]:
            msg = res['errorMessage']
            raise InvalidQueryError(
                f"Server did not accept DataRow creation request. {msg}")

        # Fetch and return the task.
        task_id = res["taskId"]
        user = self.client.get_user()
        task = list(user.created_tasks(where=Entity.Task.uid == task_id))
        # Cache user in a private variable as the relationship can't be
        # resolved due to server-side limitations (see Task.created_by)
        # for more info.
        if len(task) != 1:
            raise ResourceNotFoundError(Entity.Task, task_id)
        task = task[0]
        task._user = user
        return task

    def data_rows_for_external_id(self, external_id, limit=10):
        """ Convenience method for getting a single `DataRow` belonging to this
        `Dataset` that has the given `external_id`.

        Args:
            external_id (str): External ID of the sought `DataRow`.
            limit (int): The maximum number of data rows to return for the given external_id

        Returns:
            A single `DataRow` with the given ID.

        Raises:
            labelbox.exceptions.ResourceNotFoundError: If there is no `DataRow`
                in this `DataSet` with the given external ID, or if there are
                multiple `DataRows` for it.
        """
        DataRow = Entity.DataRow
        where = DataRow.external_id == external_id

        data_rows = self.data_rows(where=where)
        # Get at most `limit` data_rows.
        data_rows = list(islice(data_rows, limit))

        if not len(data_rows):
            raise ResourceNotFoundError(DataRow, where)
        return data_rows

    def data_row_for_external_id(self, external_id):
        """ Convenience method for getting a single `DataRow` belonging to this
        `Dataset` that has the given `external_id`.

        Args:
            external_id (str): External ID of the sought `DataRow`.

        Returns:
            A single `DataRow` with the given ID.

        Raises:
            labelbox.exceptions.ResourceNotFoundError: If there is no `DataRow`
                in this `DataSet` with the given external ID, or if there are
                multiple `DataRows` for it.
        """
        data_rows = self.data_rows_for_external_id(external_id=external_id,
                                                   limit=2)
        if len(data_rows) > 1:
            logger.warning(
                f"More than one data_row has the provided external_id : `%s`. Use function data_rows_for_external_id to fetch all",
                external_id)
        return data_rows[0]

    def export_data_rows(self, timeout_seconds=120):
        """ Returns a generator that produces all data rows that are currently attached to this dataset.

        Args:
            timeout_seconds (float): Max waiting time, in seconds.
        Returns:
            Generator that yields DataRow objects belonging to this dataset.
        Raises:
            LabelboxError: if the export fails or is unable to download within the specified time.
        """
        id_param = "datasetId"
        query_str = """mutation GetDatasetDataRowsExportUrlPyApi($%s: ID!)
            {exportDatasetDataRows(data:{datasetId: $%s }) {downloadUrl createdAt status}}
        """ % (id_param, id_param)
        sleep_time = 2
        while True:
            res = self.client.execute(query_str, {id_param: self.uid})
            res = res["exportDatasetDataRows"]
            if res["status"] == "COMPLETE":
                download_url = res["downloadUrl"]
                response = requests.get(download_url)
                response.raise_for_status()
                reader = ndjson.reader(StringIO(response.text))
                return (
                    Entity.DataRow(self.client, result) for result in reader)
            elif res["status"] == "FAILED":
                raise LabelboxError("Data row export failed.")

            timeout_seconds -= sleep_time
            if timeout_seconds <= 0:
                raise LabelboxError(
                    f"Unable to export data rows within {timeout_seconds} seconds."
                )

            logger.debug("Dataset '%s' data row export, waiting for server...",
                         self.uid)
            time.sleep(sleep_time)
