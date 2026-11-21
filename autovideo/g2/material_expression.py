"""Let an established picture hand over to its already-planned explanation."""


def keep_planned_explanation(page: dict, group: dict, shown: set[int]) -> bool:
    """A repeated single image need not replace a planned comparison.

    Keep first encounters, direct picture pairs and explicit region tours in
    their material form. No text classifier, topic vocabulary or new plan is
    inferred here; the native explanation must already exist in the storyboard.
    """
    images = group.get('image_indices', [])
    return (len(images) == 1 and images[0] in shown
            and page['component'] == 'comparison'
            and not any('region_index' in cue for cue in page.get('material_cues', [])))
