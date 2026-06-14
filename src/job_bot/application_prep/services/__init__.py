"""Services for the application_prep slice.

Issue #77: the cover-letter, relevance, and application services used to live in
``hh_applicant_tool.services``; they have been moved into this slice
as ``cover_letter_service`` / ``relevance_service`` / ``application_service``
and the legacy modules are kept as deprecation shims.
"""
