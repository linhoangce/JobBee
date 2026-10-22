"""The submit guard decides from element facts alone, so it is testable without a browser."""

from __future__ import annotations

import pytest

from app.apply.guards import (
    ControlFacts,
    final_submit_reason,
    key_would_submit,
    url_refusal_reason,
)


def _facts(label: str, **overrides) -> ControlFacts:
    base: ControlFacts = {
        "found": True,
        "tag": "BUTTON",
        "type": "submit",
        "role": "",
        "label": label,
        "is_clickable": True,
        "in_form": True,
        "container": "form",
        "container_is_search": False,
        "container_file_inputs": 1,
        "container_fillable": 8,
        "container_password_inputs": 0,
        "container_textareas": 1,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


@pytest.mark.parametrize(
    "label",
    [
        "Submit application",
        "Submit Application",
        "Send application",
        "Submit my application",
        "Complete application",
        "Submit",
        "Send",
        "Finish",
    ],
)
def test_blocks_final_submit_in_application_form(label: str) -> None:
    assert final_submit_reason(_facts(label)) is not None


def test_blocks_linkedin_review_step_with_no_inputs() -> None:
    """Easy Apply's last step is a review screen: a dialog/form with no fields left."""

    for container in ("dialog", "form"):
        facts = _facts(
            "Submit application",
            container=container,
            in_form=container == "form",
            container_file_inputs=0,
            container_fillable=0,
            container_textareas=0,
        )
        assert final_submit_reason(facts) is not None


def test_blocks_workday_bare_submit_on_review_page() -> None:
    facts = _facts(
        "Submit",
        type="button",
        in_form=False,
        container="page",
        container_file_inputs=0,
        container_fillable=0,
        container_textareas=0,
    )
    assert final_submit_reason(facts) is not None


@pytest.mark.parametrize("label", ["Next", "Continue", "Save and Continue", "Review", "Back"])
def test_allows_wizard_navigation(label: str) -> None:
    assert final_submit_reason(_facts(label)) is None


def test_allows_apply_entry_button_on_job_posting() -> None:
    facts = _facts(
        "Apply",
        type="button",
        in_form=False,
        container="page",
        container_file_inputs=0,
        container_fillable=1,
        container_textareas=0,
    )
    assert final_submit_reason(facts) is None


def test_blocks_apply_button_that_submits_a_filled_form() -> None:
    assert final_submit_reason(_facts("Apply")) is not None


def test_allows_site_search_submit() -> None:
    """RBC regression: a careers-site search 'Submit' must not be treated as the final submit."""

    facts = _facts(
        "Submit",
        container_file_inputs=0,
        container_fillable=1,
        container_textareas=0,
    )
    assert final_submit_reason(facts) is None
    assert final_submit_reason(_facts("Submit", container_is_search=True)) is None


def test_allows_sign_in_and_create_account_forms() -> None:
    for label in ("Submit", "Create Account", "Sign In"):
        facts = _facts(
            label,
            container_file_inputs=0,
            container_fillable=3,
            container_password_inputs=2,
            container_textareas=0,
        )
        assert final_submit_reason(facts) is None


def test_strong_label_blocked_even_in_account_like_container() -> None:
    facts = _facts(
        "Submit application",
        container_file_inputs=0,
        container_fillable=2,
        container_password_inputs=1,
        container_textareas=0,
    )
    assert final_submit_reason(facts) is not None


def test_unlabelled_submit_input_in_application_form_is_blocked() -> None:
    facts = _facts("", tag="INPUT", type="submit")
    assert final_submit_reason(facts) is not None


def test_non_clickable_or_missing_targets_are_allowed() -> None:
    assert final_submit_reason(None) is None
    assert final_submit_reason({"found": False}) is None
    assert final_submit_reason(_facts("Submit", is_clickable=False)) is None


def test_enter_in_application_field_is_blocked() -> None:
    field = _facts("", tag="INPUT", type="text", is_clickable=False)
    assert key_would_submit("Enter", field) is not None
    assert key_would_submit("Tab", field) is None


def test_enter_in_textarea_or_search_is_allowed() -> None:
    textarea = _facts("", tag="TEXTAREA", type="", is_clickable=False)
    assert key_would_submit("Enter", textarea) is None
    search = _facts(
        "",
        tag="INPUT",
        type="search",
        is_clickable=False,
        container_is_search=True,
    )
    assert key_would_submit("Enter", search) is None


def test_space_or_enter_on_focused_submit_button_is_blocked() -> None:
    button = _facts("Submit application")
    assert key_would_submit("space", button) is not None
    assert key_would_submit("Return", button) is not None


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///C:/Users/me/.ssh/id_rsa",
        "data:text/html,hi",
        "http://localhost:8000/api/profile",
        "http://127.0.0.1:8000",
        "http://192.168.1.10/admin",
        "http://10.0.0.5",
        "http://169.254.169.254/latest/meta-data",
        "chrome://settings",
    ],
)
def test_url_policy_refuses_dangerous_targets(url: str) -> None:
    assert url_refusal_reason(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/acme/jobs/123",
        "jobs.lever.co/acme/abc",
        "https://bmo.wd3.myworkdayjobs.com/en-US/External/job/x",
        "back",
        "about:blank",
    ],
)
def test_url_policy_allows_job_sites(url: str) -> None:
    assert url_refusal_reason(url) is None


def test_enter_inside_uninspectable_frame_is_blocked() -> None:
    facts: ControlFacts = {"found": True, "cross_origin_frame": True}
    assert key_would_submit("Enter", facts) is not None
    assert key_would_submit("Tab", facts) is None
    # Clicks into such frames can't be classified; the driver logs them instead.
    assert final_submit_reason(facts) is None
