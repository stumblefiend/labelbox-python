from typing import Optional, TypedDict
from labelbox_dev.entity import Entity
from labelbox_dev.session import Session

DATASET_RESOURCE = "datasets"


class CreateDatasetType(TypedDict):
    id: Optional[str]
    name: str
    description: Optional[str]


class UpdateDatasetType(TypedDict):
    name: Optional[str]
    description: Optional[str]


def get_by_id(dataset_id: str):
    dataset_json = Session.get_request(f"{DATASET_RESOURCE}/{dataset_id}")
    return Dataset(dataset_json)


def create(dataset: CreateDatasetType):
    dataset_json = Session.post_request(f"{DATASET_RESOURCE}", json=dataset)
    return Dataset(dataset_json)


class Dataset(Entity):

    def __init__(self, json):
        super().__init__(json)
        self.from_json(json)

    def from_json(self, json) -> "Dataset":
        super().from_json(json)
        self.id = json['id']
        self.name = json['name']
        self.description = json['description']
        self.created_at = json['created_at']
        self.updated_at = json['updated_at']
        self.created_by_id = json['created_by_id']
        self.organization_id = json['organization_id']
        self.data_row_count = json['data_row_count']

        return self

    def delete(self) -> None:
        Session.delete_request(f"{DATASET_RESOURCE}/{self.id}")

    def update(self, dataset_update: UpdateDatasetType) -> "Dataset":
        dataset_json = Session.patch_request(f"{DATASET_RESOURCE}/{self.id}",
                                             json=dataset_update)
        return self.from_json(dataset_json)
