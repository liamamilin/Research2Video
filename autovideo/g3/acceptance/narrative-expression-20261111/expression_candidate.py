"""Candidate: retain established material context, then honor the planned explanation."""


def release_material(page: dict, group: dict, shown: set[int]) -> bool:
    images = group.get('image_indices', [])
    return (len(images) == 1 and images[0] in shown
            and page['component'] in {'comparison', 'flow_diagram'}
            and not any('region_index' in cue for cue in page.get('material_cues', [])))
