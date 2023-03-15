import uuid
from labelbox.data.annotation_types.annotation import ObjectAnnotation
from labelbox.data.annotation_types.label import Label
from labelbox.data.annotation_types.data.text import TextData
from labelbox.data.annotation_types.ner import ConversationEntity

from labelbox.schema.annotation_import import MALPredictionImport


def test_conversation_entity(client, configured_project_without_data_rows,
                             dataset_conversation_entity, rand_gen):

    conversation_entity_annotation = ConversationEntity(start=0,
                                                        end=8,
                                                        message_id="4")

    entities_annotation = ObjectAnnotation(name="named-entity",
                                           value=conversation_entity_annotation)

    labels = []
    _, data_row_uids = dataset_conversation_entity
    configured_project_without_data_rows.create_batch(
        rand_gen(str),
        data_row_uids,  # sample of data row objects
        5  # priority between 1(Highest) - 5(lowest)
    )

    for data_row_uid in data_row_uids:
        labels.append(
            Label(data=TextData(uid=data_row_uid),
                  annotations=[
                      entities_annotation,
                  ]))

    import_annotations = MALPredictionImport.create_from_objects(
        client=client,
        project_id=configured_project_without_data_rows.uid,
        name=f"import {str(uuid.uuid4())}",
        predictions=labels)
    import_annotations.wait_until_done()

    assert import_annotations.errors == []

    exported_labels = configured_project_without_data_rows.label_generator()
    for label in exported_labels:
        assert len(
            label.annotations) == 1  # we have created only 1 annotation above
        annotation = label.annotations[0]

        assert type(annotation) is ConversationEntity
        assert annotation.name == "named-entity"
        assert annotation.value.message_id == "4"
        assert annotation.value.start == 0
        assert annotation.value.end == 8
