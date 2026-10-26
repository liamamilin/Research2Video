"""Optional visual sequencing metadata over the unchanged narration timeline."""
COMPONENTS = {'title_card', 'concept_reveal', 'comparison', 'big_number', 'bar_chart', 'price_change',
              'source_card', 'data_chart', 'scatter_chart', 'flow_diagram', 'object_showcase', 'material_collage'}


def flow_schema() -> dict:
    return {'type': 'object', 'additionalProperties': False,
        'required': ['version', 'profile', 'entry', 'paragraph_ids', 'part_index', 'part_count'],
        'properties': {'version': {'const': 1}, 'profile': {'enum': ['brief', 'balanced', 'explain']},
            'entry': {'enum': ['cut', 'dissolve', 'carry']},
            'paragraph_ids': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'uniqueItems': True,
                              'items': {'type': 'string', 'minLength': 1, 'maxLength': 80}},
            'part_index': {'type': 'integer', 'minimum': 1, 'maximum': 10000},
            'part_count': {'type': 'integer', 'minimum': 1, 'maximum': 10000}}}
