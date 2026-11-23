"""Shared fixtures of the answer-path tests: one private PostgreSQL holding both demo models and their projections."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Recorded one-call replies (catalog, ingest and dev-set tests) were made with `hybrid12`; the default is `two_step`.
# Set at import so that module- and session-scoped fixtures see it too. Tests of other methods pass `variant=`.
os.environ["V2_CANDIDATE_VARIANT"] = "hybrid12"


@pytest.fixture(scope="session")
def answer_pg(tmp_path_factory):
    """A private PostgreSQL with the finance and manufacturing models imported and their CSV projections loaded."""
    if not os.environ.get("V2_PG_BIN"):
        pytest.skip("set V2_PG_BIN to run the answer-path tests")
    from semantic_pipeline_v2._pilot_postgres import PilotPostgres
    from semantic_pipeline_v2.metric_sql import ensure_projections_loaded
    from semantic_pipeline_v2.semantic_store import SemanticStore

    with PilotPostgres.start(Path(os.environ["V2_PG_BIN"]),
                             data_directory=tmp_path_factory.mktemp("answer_pg") / "pgdata") as pg:
        store = SemanticStore(pg)
        store.import_ossie("financial-services", ROOT / "demo_data/finance/finance.ossie.yaml")
        store.import_ossie("manufacturing", ROOT / "demo_data/manufacturing/semantics/manufacturing.ossie.yaml")
        ensure_projections_loaded(pg, "financial-services", ROOT / "demo_data/finance/local/finance_workbook")
        ensure_projections_loaded(pg, "manufacturing", ROOT / "demo_data/manufacturing/data/csv")
        yield pg



# Added by amp/export.py: tests that need material this public repository does not ship skip with the reason.
_EXPORT_SKIPS = {
    "test_output_c_sources.py::test_a_recording_needs_exactly_one_approved_customer_id_and_a_recording": "needs a customer file whose inventory entry is not published with this repository",
    "test_output_c_sources.py::test_the_preview_suggests_the_other_sources_whose_evidence_fills_the_same_tables": "needs a customer file whose inventory entry is not published with this repository",
    "test_output_c_sources.py::test_a_recording_suggests_only_other_recordings_that_can_be_written": "needs a customer file whose inventory entry is not published with this repository",
    "test_output_c_sources.py::test_the_suggested_group_carries_a_model_written_hint_built_from_the_evidence": "needs a customer file whose inventory entry is not published with this repository",
    "test_output_c_sources.py::test_no_hint_without_a_suggestion_a_model_or_a_usable_reply": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_the_schema_is_proposed_from_the_labels_with_types_a_section_prefix_on_a_clash_and_system_columns": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_a_value_that_fails_its_type_stays_text_and_a_leading_zero_is_not_a_number": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_the_same_label_set_gives_the_same_table_and_another_set_or_layout_a_different_one": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_two_files_with_the_same_labels_write_one_table_with_two_rows_and_the_group_is_suggested": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_an_existing_table_with_the_same_columns_appends_and_with_other_columns_is_refused": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_the_trino_statements_create_the_table_once_and_insert_only_missing_keys": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_to_a_table_keeps_a_declared_match_and_falls_back_only_with_three_fields": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_a_reader_is_refused": "needs a customer file whose inventory entry is not published with this repository",
    "test_discovered_table.py::test_a_definition_id_is_recorded_only_on_a_column_whose_label_a_link_quotes": "needs a customer file whose inventory entry is not published with this repository",
    "test_physical_printed.py::test_a_form_and_a_printed_report_write_rows_of_both_into_the_same_tables": "needs a customer file whose inventory entry is not published with this repository",
    "test_physical_printed.py::test_the_suggested_group_offers_a_printed_report_next_to_a_form_and_the_reverse": "needs a customer file whose inventory entry is not published with this repository",
    "test_ocr.py::test_http_checks_admin_source_original_and_retrieves_mapped_evidence": "needs a customer file whose inventory entry is not published with this repository",
    "test_originals.py::test_original_audio_ranges_and_office_download": "needs a customer file whose inventory entry is not published with this repository",
    "test_transcribe.py::test_map_pages_keeps_time_ranges_and_chunks_reach_links": "needs a customer file whose inventory entry is not published with this repository",
    "test_transcribe.py::test_http_failure_is_an_error_not_a_sample": "needs a customer file whose inventory entry is not published with this repository",
    "test_fresh_clone_paths.py::test_local_originals_folder_comes_from_the_environment": "needs a customer file whose inventory entry is not published with this repository",
    "test_fresh_clone_paths.py::test_local_originals_default_to_downloads": "needs a customer file whose inventory entry is not published with this repository",
    "test_concept_induction.py::test_finance_chunks_include_xfa_forms_and_are_masked": "needs a customer file whose inventory entry is not published with this repository",
    "test_concept_induction.py::test_missing_pinned_pdf_is_reported_not_skipped": "needs a customer file whose inventory entry is not published with this repository",
    "test_workspace.py::test_finance_preserves_inventory_without_private_source_citations_or_identity": "needs a customer file whose inventory entry is not published with this repository",
    "test_concept_catalog.py::test_finance_chunks_now_include_the_three_text_layer_pdfs_masked_and_hash_checked": "needs a customer file whose inventory entry is not published with this repository",
    "test_concept_catalog.py::test_a_changed_text_layer_pdf_is_reported_not_skipped": "needs a customer file whose inventory entry is not published with this repository",
    "test_candidates.py::test_adopted_variant_replays_recorded_replies_on_gold_a2": "reads the private gold evaluation fixtures, which are not published",
    "test_candidates.py::test_offered_only_needs_no_chat_and_unknown_gold_keys_and_categories_are_kept": "reads the private gold evaluation fixtures, which are not published",
    "test_concept_catalog.py::test_e5_schema_lint_identity_and_reimport": "needs the Helios Ossie schema from a sibling checkout, which is not shipped",
    "test_concept_catalog.py::test_recorded_replies_rebuild_the_manufacturing_catalog_offline": "needs the Helios Ossie schema from a sibling checkout, which is not shipped",
    "test_concept_catalog.py::test_the_export_round_trip_keeps_document_synonyms_of_a_both_record": "needs the Helios Ossie schema from a sibling checkout, which is not shipped",
    "test_catalog_anchored.py::test_e22_a_candidate_stays_in_the_catalog_and_the_store_but_is_not_exported": "needs the Helios Ossie schema from a sibling checkout, which is not shipped",
    "test_catalog_anchored.py::test_recorded_the_anchored_catalog_of_manufacturing_replays_offline_and_exports_clean": "needs the Helios Ossie schema from a sibling checkout, which is not shipped",
    "test_ask_actions.py::test_ask_names_a_form_and_returns_a_preview_without_writing": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_hints_live.py::test_a_reader_asking_to_write_gets_the_writers_refusal_and_no_clarification": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_hints_live.py::test_a_request_that_names_a_declared_table_offers_the_files_with_rows_of_it": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_hints_live.py::test_the_flat_clarification_puts_the_named_recording_first_and_one_chip_per_file": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_semantic.py::test_a_file_named_by_its_number_or_a_distinctive_word_passes_the_guard": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_semantic.py::test_a_model_definition_and_a_table_name_are_matched_as_well": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_semantic.py::test_a_withheld_source_is_never_named": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_semantic.py::test_all_of_these_is_a_write_only_right_after_a_semantic_clarification": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_semantic.py::test_ask_returns_the_semantic_clarification_and_all_of_these_previews_the_files_with_rows": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_clarify_semantic.py::test_the_clarification_names_the_concept_the_files_that_state_it_and_the_files_with_rows": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_router_sentences.py::test_a_model_routed_write_returns_a_preview_and_writes_nothing": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_ask_router_sentences.py::test_ask_shows_how_the_route_was_chosen_and_a_clarification_is_an_answer_status": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_iceberg_target.py::test_plain_confirm_never_calls_cde_and_a_cde_failure_keeps_postgres_and_reports_the_job": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_iceberg_target.py::test_preview_states_the_iceberg_target_and_confirm_writes_postgres_and_submits_one_job_once": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_iceberg_target.py::test_without_a_target_the_preview_names_private_postgresql_only": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_physical_mapping.py::test_e13_preview_then_confirm_writes_the_expected_rows_with_provenance_and_talk_to_data_answers": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_physical_mapping.py::test_e14_reader_gets_403_untemplated_type_offers_no_write_and_bad_or_unlinked_values_are_not_written": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_physical_second_table.py::test_preview_and_confirm_write_both_tables_for_both_forms_and_a_join_question_answers": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_table_choice.py::test_a_table_is_matched_by_any_word_that_names_exactly_one_declared_table": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_table_choice.py::test_a_table_name_that_matches_no_declared_table_is_refused_by_name": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_table_choice.py::test_one_named_table_is_previewed_and_written_alone": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_trino_target.py::test_the_form_rows_panel_writes_through_trino_and_shows_no_spark_job_state": "written around the customer's form and call files, which are not published (the export renames them)",
    "test_extra_sources.py::test_export_is_current": "checks docs/design/v2-workspace/workspace-data.js, which the server writes at start and is not shipped",
    "test_workspace.py::test_cli_catalog_and_export_freshness": "checks docs/design/v2-workspace/workspace-data.js, which the server writes at start and is not shipped"
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        reason = _EXPORT_SKIPS.get(item.path.name + "::" + item.originalname)
        if reason:
            item.add_marker(pytest.mark.skip(reason=reason))
