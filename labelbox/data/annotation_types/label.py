from typing import Union, List

from labelbox.schema.ontology import OntologyBuilder
from labelbox.data.annotation_types.annotation import Annotation
from labelbox.data.annotation_types.data.raster import RasterData
from labelbox.data.annotation_types.data.text import TextData
from labelbox.data.annotation_types.metrics import Metric
from pydantic import BaseModel


class Label(BaseModel):
    # TODO: This sounds too much like the other label we need to rename this...
    data: Union[RasterData, TextData]
    annotations: List[Union[Annotation, Metric]] = []

    def to_mal_ndjson(self, ontology: OntologyBuilder):
        if self.data.data_row_ref.uid is None:
            raise ValueError(
                "Must create reference to data row to use data for mal")

        ndjsons = [
            annotation.to_mal_ndjson() for annotation in self.annotations
        ]
        for ndjson in ndjsons:
            ndjson.update({"dataRow": {"id": self.data.data_row_ref.uid}})
        return ndjsons
