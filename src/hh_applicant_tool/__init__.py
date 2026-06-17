import warnings

warnings.warn(
    "hh_applicant_tool is deprecated; use job_bot instead. "
    "The package will be removed in 2.0.",
    DeprecationWarning,
    stacklevel=2,
)
__all__ = ["__version__"]
__version__ = "2.0.0"
