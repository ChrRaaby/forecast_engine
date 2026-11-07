"""Experimental forecast-prompt variants (B-75(2), Christian 2026-11-07). One module per prompt version, each with
`forecast_prompt(q, research, today) -> str`, registered in `forecast_engine/prompt_versions.py`. A variant is used only by a
config whose `prompt_version` names it (a replay arm or an unpublished shadow arm); the published configs always use the live
prompt in `forecast_engine/prompts.py`. A variant may import helpers from prompts.py; once registered and archived
(`evals/prompt_archive.py`), a variant module is never edited: a changed prompt is a new version in a new module.
"""
