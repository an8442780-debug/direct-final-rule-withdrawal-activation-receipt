import hashlib
import os
import tempfile

from gltest.direct import loader as direct_loader
from gltest.direct.sdk_compat import import_address, import_calldata
from gltest.direct.vm import VMContext


def _inject_message_to_fd0_without_windows_unlink_race(vm):
    """Keep the pinned direct runner usable on Windows.

    The bundled loader unlinks the file while fd 0 still owns it. Windows
    rejects that operation; keep the path until VM cleanup restores stdin.
    """
    calldata = import_calldata()
    Address = import_address()
    sender = vm.sender
    contract = vm._contract_address
    origin = vm.origin
    if isinstance(sender, bytes):
        sender = Address(sender)
    if isinstance(contract, bytes):
        contract = Address(contract)
    if isinstance(origin, bytes):
        origin = Address(origin)

    message_data = {
        "contract_address": contract,
        "sender_address": sender,
        "origin_address": origin,
        "stack": [],
        "value": vm._value,
        "datetime": vm._datetime,
        "is_init": False,
        "chain_id": vm._chain_id,
        "entry_kind": 0,
        "entry_data": b"",
        "entry_stage_data": None,
    }
    encoded = calldata.encode(message_data)
    fd, path = tempfile.mkstemp()
    try:
        os.write(fd, encoded)
        os.lseek(fd, 0, os.SEEK_SET)
        vm._original_stdin_fd = os.dup(0)
        os.dup2(fd, 0)
        vm._direct_message_path = path
    except BaseException:
        os.close(fd)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(fd)


_original_cleanup_after_deactivate = VMContext._cleanup_after_deactivate


def _cleanup_after_deactivate_with_message_file(vm):
    _original_cleanup_after_deactivate(vm)
    path = getattr(vm, "_direct_message_path", None)
    if path:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        vm._direct_message_path = None


direct_loader._inject_message_to_fd0 = _inject_message_to_fd0_without_windows_unlink_race
VMContext._cleanup_after_deactivate = _cleanup_after_deactivate_with_message_file


CONTRACT = "contracts/direct_final_rule_withdrawal_activation_receipt.py"
AGENCY = "Food and Drug Administration"
DOCKET = "FDA-2024-N-3654"
RIN = "0910-AI97"
ORIGIN_DOC = "2024-21231"
ORIGIN_CITATION = "89 FR 77019"


def source_hash(kind, fr_doc, effective_day):
    marker = {
        "ORIGINATING": "PENDING_CONDITIONAL",
        "CORRECTION": "CORRECTED_EFFECTIVE_DATE",
        "WITHDRAWAL": "WITHDRAWN_SIGNIFICANT_ADVERSE_COMMENT",
    }[kind]
    canonical = "|".join((kind, fr_doc, AGENCY, DOCKET, RIN, ORIGIN_CITATION, effective_day, marker))
    return hashlib.sha256(canonical.encode()).hexdigest()


def add_source(contract, lineage_id, kind, fr_doc, url, effective_day, body):
    contract.add_instrument(
        lineage_id,
        kind,
        fr_doc,
        url,
        source_hash(kind, fr_doc, effective_day),
        "2026-09-21",
        effective_day,
    )


def origin_body():
    return (
        f"{AGENCY} Docket No. {DOCKET} RIN {RIN}. "
        f"Direct final rule FR Doc. {ORIGIN_DOC}. "
        f"Published at {ORIGIN_CITATION}. "
        "This direct final rule is effective February 3, 2025. "
        "If timely significant adverse comments are received, FDA will publish "
        "a document withdrawing this direct final rule."
    )


def correction_body():
    return (
        f"{AGENCY} Docket No. {DOCKET} RIN {RIN}. "
        f"Direct final rule correction, FR Doc. 2024-24100, corrects the effective date "
        f"for the direct final rule published at {ORIGIN_CITATION}. "
        "Effective date: February 3, 2025."
    )


def withdrawal_body():
    return (
        f"{AGENCY} Docket No. {DOCKET} RIN {RIN}. "
        "Direct final rule withdrawal, FR Doc. 2025-01145. "
        f"The direct final rule published at {ORIGIN_CITATION} is withdrawn. "
        "FDA is withdrawing the direct final rule because the Agency received "
        "significant adverse comment. The direct final rule is withdrawn effective "
        "January 17, 2025."
    )


def test_withdrawal_overrides_corrected_effective_date(direct_vm, direct_deploy):
    direct_vm.strict_mocks = True
    urls = {
        "ORIGINATING": "https://www.federalregister.gov/test/origin",
        "CORRECTION": "https://www.federalregister.gov/test/correction",
        "WITHDRAWAL": "https://www.federalregister.gov/test/withdrawal",
    }
    direct_vm.mock_web(r".*test/origin", {"status": 200, "body": origin_body()})
    direct_vm.mock_web(r".*test/correction", {"status": 200, "body": correction_body()})
    direct_vm.mock_web(r".*test/withdrawal", {"status": 200, "body": withdrawal_body()})

    contract = direct_deploy(CONTRACT)
    lineage_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    add_source(contract, lineage_id, "ORIGINATING", ORIGIN_DOC, urls["ORIGINATING"], "2025-02-03", origin_body())
    add_source(contract, lineage_id, "CORRECTION", "2024-24100", urls["CORRECTION"], "2025-02-03", correction_body())
    add_source(contract, lineage_id, "WITHDRAWAL", "2025-01145", urls["WITHDRAWAL"], "2025-01-17", withdrawal_body())
    contract.seal_lineage(lineage_id)

    assessment_id = contract.assess_activation(lineage_id, "2025-02-04")
    assessment = contract.get_assessment(lineage_id)

    assert assessment_id == 1
    assert assessment["status"] == "WITHDRAWN"
    assert assessment["terminal_action"] == "WITHDRAWN"
    assert assessment["terminal_effective_day"] == "2025-01-17"
    assert assessment["rule_active"] == "False"
    assert contract.is_rule_active(lineage_id) is False
    assert contract.assess_activation(lineage_id, "2025-02-04") == assessment_id
    assert contract.get_lineage(lineage_id)["assessment_count"] == "1"


def test_pending_then_effective_without_withdrawal(direct_vm, direct_deploy):
    direct_vm.mock_web(r".*pending/origin", {"status": 200, "body": origin_body()})
    direct_vm.mock_web(r".*pending/correction", {"status": 200, "body": correction_body()})

    contract = direct_deploy(CONTRACT)
    lineage_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    add_source(
        contract,
        lineage_id,
        "ORIGINATING",
        ORIGIN_DOC,
        "https://www.federalregister.gov/test/pending/origin",
        "2025-02-03",
        origin_body(),
    )
    add_source(
        contract,
        lineage_id,
        "CORRECTION",
        "2024-24100",
        "https://www.federalregister.gov/test/pending/correction",
        "2025-02-03",
        correction_body(),
    )
    contract.seal_lineage(lineage_id)

    contract.assess_activation(lineage_id, "2025-01-01")
    assert contract.get_assessment(lineage_id)["status"] == "PENDING"
    assert contract.is_rule_active(lineage_id) is False

    contract.assess_activation(lineage_id, "2025-02-04")
    assert contract.get_assessment(lineage_id)["status"] == "EFFECTIVE"
    assert contract.is_rule_active(lineage_id) is True

    with direct_vm.expect_revert("ASSESSMENT_DAY_OUT_OF_ORDER"):
        contract.assess_activation(lineage_id, "2025-01-15")


def test_expected_effective_day_is_enforced_for_non_withdrawal_sources(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    lineage_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    add_source(
        contract,
        lineage_id,
        "ORIGINATING",
        ORIGIN_DOC,
        "https://www.federalregister.gov/test/date/origin",
        "2025-02-03",
        origin_body(),
    )

    with direct_vm.expect_revert("EXPECTED_EFFECTIVE_DAY_MISMATCH"):
        contract.add_instrument(
            lineage_id,
            "CORRECTION",
            "2024-24100",
            "https://www.federalregister.gov/test/date/correction",
            source_hash("CORRECTION", "2024-24100", "2025-02-04"),
            "2026-09-21",
            "2025-02-04",
        )

    assert contract.get_lineage(lineage_id)["instrument_count"] == "1"


def test_identity_mismatch_fails_closed(direct_vm, direct_deploy):
    direct_vm.mock_web(r".*mismatch/origin", {"status": 200, "body": origin_body()})
    direct_vm.mock_web(
        r".*mismatch/correction",
        {"status": 200, "body": correction_body().replace(DOCKET, "FDA-WRONG-DOCKET")},
    )

    contract = direct_deploy(CONTRACT)
    lineage_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    add_source(
        contract,
        lineage_id,
        "ORIGINATING",
        ORIGIN_DOC,
        "https://www.federalregister.gov/test/mismatch/origin",
        "2025-02-03",
        origin_body(),
    )
    add_source(
        contract,
        lineage_id,
        "CORRECTION",
        "2024-24100",
        "https://www.federalregister.gov/test/mismatch/correction",
        "2025-02-03",
        correction_body(),
    )
    contract.seal_lineage(lineage_id)

    contract.assess_activation(lineage_id, "2025-02-04")
    assessment = contract.get_assessment(lineage_id)
    assert assessment["status"] == "UNRESOLVED"
    assert assessment["rule_active"] == "False"
    assert contract.is_rule_active(lineage_id) is False


def test_source_hash_mismatch_fails_closed(direct_vm, direct_deploy):
    direct_vm.mock_web(r".*hash/origin", {"status": 200, "body": origin_body()})
    direct_vm.mock_web(r".*hash/correction", {"status": 200, "body": correction_body()})

    contract = direct_deploy(CONTRACT)
    lineage_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    add_source(
        contract,
        lineage_id,
        "ORIGINATING",
        ORIGIN_DOC,
        "https://www.federalregister.gov/test/hash/origin",
        "2025-02-03",
        origin_body(),
    )
    contract.add_instrument(
        lineage_id,
        "CORRECTION",
        "2024-24100",
        "https://www.federalregister.gov/test/hash/correction",
        "0" * 64,
        "2026-09-21",
        "2025-02-03",
    )
    contract.seal_lineage(lineage_id)

    contract.assess_activation(lineage_id, "2025-02-04")
    assessment = contract.get_assessment(lineage_id)
    assert assessment["status"] == "UNRESOLVED"
    assert assessment["reason_code"] == "SOURCE_HASH_MISMATCH"
    assert assessment["rule_active"] == "False"
    assert contract.is_rule_active(lineage_id) is False


def test_non_success_source_fails_closed(direct_vm, direct_deploy):
    direct_vm.mock_web(r".*status/origin", {"status": 404, "body": origin_body()})
    direct_vm.mock_web(r".*status/correction", {"status": 200, "body": correction_body()})

    contract = direct_deploy(CONTRACT)
    lineage_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    add_source(
        contract,
        lineage_id,
        "ORIGINATING",
        ORIGIN_DOC,
        "https://www.federalregister.gov/test/status/origin",
        "2025-02-03",
        origin_body(),
    )
    add_source(
        contract,
        lineage_id,
        "CORRECTION",
        "2024-24100",
        "https://www.federalregister.gov/test/status/correction",
        "2025-02-03",
        correction_body(),
    )
    contract.seal_lineage(lineage_id)

    contract.assess_activation(lineage_id, "2025-02-04")
    assessment = contract.get_assessment(lineage_id)
    assert assessment["status"] == "UNRESOLVED"
    assert assessment["reason_code"] == "SOURCE_HTTP_STATUS"
    assert assessment["rule_active"] == "False"
    assert contract.is_rule_active(lineage_id) is False


def test_owner_auth_and_supersession(direct_vm, direct_deploy, direct_bob, direct_owner):
    contract = direct_deploy(CONTRACT)
    lineage_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("OWNER_ONLY"):
        contract.add_instrument(
            lineage_id,
            "ORIGINATING",
            ORIGIN_DOC,
            "https://www.federalregister.gov/test/auth",
            source_hash("ORIGINATING", ORIGIN_DOC, "2025-02-03"),
            "2026-09-21",
            "2025-02-03",
        )
    direct_vm.sender = direct_owner

    replacement_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    for current_id in (lineage_id, replacement_id):
        add_source(
            contract,
            current_id,
            "ORIGINATING",
            ORIGIN_DOC,
            f"https://www.federalregister.gov/test/supersede/{current_id}/origin",
            "2025-02-03",
            origin_body(),
        )
        add_source(
            contract,
            current_id,
            "CORRECTION",
            "2024-24100",
            f"https://www.federalregister.gov/test/supersede/{current_id}/correction",
            "2025-02-03",
            correction_body(),
        )
        contract.seal_lineage(current_id)

    contract.supersede_lineage(lineage_id, replacement_id)
    assert contract.get_lineage(lineage_id)["state"] == "SUPERSEDED"
    assert contract.get_lineage(lineage_id)["superseded_by"] == str(replacement_id)


def test_cross_identity_supersession_fails_closed(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    lineage_id = contract.create_lineage(AGENCY, DOCKET, RIN, ORIGIN_DOC, ORIGIN_CITATION, "2025-02-03")
    replacement_id = contract.create_lineage(
        AGENCY,
        "FDA-WRONG-DOCKET",
        RIN,
        ORIGIN_DOC,
        ORIGIN_CITATION,
        "2025-02-03",
    )

    for current_id in (lineage_id, replacement_id):
        add_source(
            contract,
            current_id,
            "ORIGINATING",
            ORIGIN_DOC,
            f"https://www.federalregister.gov/test/cross-identity/{current_id}/origin",
            "2025-02-03",
            origin_body(),
        )
        add_source(
            contract,
            current_id,
            "CORRECTION",
            "2024-24100",
            f"https://www.federalregister.gov/test/cross-identity/{current_id}/correction",
            "2025-02-03",
            correction_body(),
        )
        contract.seal_lineage(current_id)

    with direct_vm.expect_revert("LINEAGE_IDENTITY_MISMATCH"):
        contract.supersede_lineage(lineage_id, replacement_id)
