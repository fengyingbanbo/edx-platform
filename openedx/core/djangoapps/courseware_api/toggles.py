"""
Toggles for courseware.
"""

from lms.djangoapps.experiments.flags import ExperimentWaffleFlag
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag, WaffleFlagNamespace

WAFFLE_FLAG_NAMESPACE = WaffleFlagNamespace(name='courseware')

COURSEWARE_MICROFRONTEND = ExperimentWaffleFlag(WAFFLE_FLAG_NAMESPACE, 'courseware_mfe')

COURSEWARE_MICROFRONTEND_COURSE_COMPLETION = CourseWaffleFlag(
    WAFFLE_FLAG_NAMESPACE, 'courseware_mfe_course_completion'
)


def course_completion_is_active(course_key):
    return (
        COURSEWARE_MICROFRONTEND.is_enabled(course_key) and
        COURSEWARE_MICROFRONTEND_COURSE_COMPLETION.is_enabled(course_key)
    )
