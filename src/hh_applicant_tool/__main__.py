import sys
import warnings

from job_bot.__main__ import main

warnings.warn(
    "hh_applicant_tool.__main__ is deprecated; use 'python -m job_bot' "
    "or the hh-applicant-tool script (which now points at job_bot).",
    DeprecationWarning,
    stacklevel=2,
)
sys.exit(main())
