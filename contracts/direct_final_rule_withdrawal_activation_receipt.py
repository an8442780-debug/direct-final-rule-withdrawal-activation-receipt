# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

from dataclasses import dataclass
import hashlib
import json

import genlayer as gl
from genlayer import *

Address = gl.Address
DynArray = gl.storage.DynArray
allow_storage = gl.storage.allow


LINEAGE_DRAFT = "DRAFT"
LINEAGE_PENDING = "PENDING"
LINEAGE_EFFECTIVE = "EFFECTIVE"
LINEAGE_WITHDRAWN = "WITHDRAWN"
LINEAGE_UNRESOLVED = "UNRESOLVED"
LINEAGE_SUPERSEDED = "SUPERSEDED"

ORIGINATING = "ORIGINATING"
CORRECTION = "CORRECTION"
WITHDRAWAL = "WITHDRAWAL"


@allow_storage
@dataclass
class Lineage:
    lineage_id: u256
    agency: str
    docket: str
    rin: str
    originating_fr_doc: str
    originating_fr_citation: str
    expected_effective_day: str
    state: str
    instrument_count: u256
    assessment_count: u256
    superseded_by: u256


@allow_storage
@dataclass
class Instrument:
    lineage_id: u256
    kind: str
    fr_doc: str
    url: str
    source_hash: str
    retrieved_day: str
    effective_day: str


@allow_storage
@dataclass
class Assessment:
    assessment_id: u256
    lineage_id: u256
    as_of_day: str
    status: str
    identity_match: bool
    conditional_effect_clause: bool
    corrected_date_state: str
    terminal_action: str
    terminal_effective_day: str
    rule_active: bool
    reason_code: str
    evidence_fingerprint: str
    explanation: str


class DirectFinalRuleWithdrawalActivationReceipt(gl.contract.Contract):
    owner: Address
    next_lineage_id: u256
    next_assessment_id: u256
    lineages: DynArray[Lineage]
    instruments: DynArray[Instrument]
    assessments: DynArray[Assessment]

    def __init__(self):
        self.owner = gl.message.sender_address
        self.next_lineage_id = u256(1)
        self.next_assessment_id = u256(1)

    def _is_iso_day(self, value: str) -> bool:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            return False
        for index in (0, 1, 2, 3, 5, 6, 8, 9):
            if value[index] < "0" or value[index] > "9":
                return False
        month = int(value[5:7])
        day = int(value[8:10])
        if not 1 <= month <= 12:
            return False
        year = int(value[0:4])
        leap_year = year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)
        days_in_month = (31, 29 if leap_year else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
        return 1 <= day <= days_in_month[month - 1]

    def _is_safe_field(self, value: str) -> bool:
        if not value:
            return False
        for character in value:
            if character in "|\r\n\t":
                return False
        return True

    def _is_hex_digest(self, value: str) -> bool:
        if len(value) != 64:
            return False
        for character in value:
            if character not in "0123456789abcdef":
                return False
        return True

    def _date_text(self, value: str) -> str:
        months = (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        )
        return f"{months[int(value[5:7]) - 1]} {int(value[8:10])}, {value[0:4]}"

    def _contains_all(self, text: str, fragments: tuple[str, ...]) -> bool:
        for fragment in fragments:
            if fragment not in text:
                return False
        return True

    def _source_fingerprint(
        self,
        kind: str,
        fr_doc: str,
        agency: str,
        docket: str,
        rin: str,
        originating_fr_citation: str,
        effective_day: str,
    ) -> str:
        marker = {
            ORIGINATING: "PENDING_CONDITIONAL",
            CORRECTION: "CORRECTED_EFFECTIVE_DATE",
            WITHDRAWAL: "WITHDRAWN_SIGNIFICANT_ADVERSE_COMMENT",
        }[kind]
        canonical = "|".join(
            (kind, fr_doc, agency, docket, rin, originating_fr_citation, effective_day, marker)
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _body_text(self, response) -> str:
        body = response.body
        if isinstance(body, bytes):
            return body.decode("utf-8")
        return str(body)

    def _unresolved_result(self, reason_code: str) -> str:
        return json.dumps(
            {
                "identity_match": False,
                "conditional_effect_clause": False,
                "corrected_date_state": "",
                "terminal_action": "",
                "terminal_effective_day": "",
                "status": LINEAGE_UNRESOLVED,
                "rule_active": False,
                "reason_code": reason_code,
                "evidence_fingerprint": "",
                "explanation": "Evidence could not be independently reconciled; fail closed.",
            },
            sort_keys=True,
        )

    def _fetch_and_derive(self, source_specs: list[dict[str, str]], as_of_day: str) -> str:
        agency = source_specs[0]["agency"]
        docket = source_specs[0]["docket"]
        rin = source_specs[0]["rin"]
        origin_seen = False
        withdrawal_seen = False
        identity_match = True
        conditional_effect_clause = False
        corrected_date_state = ""
        terminal_effective_day = ""
        reason_code = ""
        source_hashes: list[str] = []

        for spec in source_specs:
            try:
                response = gl.nondet.web.get(spec["url"])
                if response.status != 200 or response.body is None:
                    return self._unresolved_result("SOURCE_HTTP_STATUS")
                body = self._body_text(response)
                normalized = " ".join(body.lower().split())
            except Exception:
                return self._unresolved_result("SOURCE_UNAVAILABLE")

            identity_ok = self._contains_all(
                normalized,
                (
                    spec["agency"].lower(),
                    spec["docket"].lower(),
                    spec["rin"].lower(),
                    spec["fr_doc"].lower(),
                    self._date_text(spec["effective_day"]).lower(),
                ),
            )
            if spec["kind"] == ORIGINATING:
                identity_ok = identity_ok and spec["originating_fr_doc"].lower() in normalized
            else:
                identity_ok = identity_ok and spec["originating_fr_citation"].lower() in normalized
            if not identity_ok:
                identity_match = False
                reason_code = "IDENTITY_OR_DATE_MISMATCH"

            expected_hash = self._source_fingerprint(
                spec["kind"],
                spec["fr_doc"],
                spec["agency"],
                spec["docket"],
                spec["rin"],
                spec["originating_fr_citation"],
                spec["effective_day"],
            )
            if expected_hash != spec["source_hash"]:
                identity_match = False
                reason_code = "SOURCE_HASH_MISMATCH"
            source_hashes.append(expected_hash)

            if spec["kind"] == ORIGINATING:
                origin_seen = True
                conditional_effect_clause = self._contains_all(
                    normalized,
                    (
                        "direct final rule",
                        "if timely significant adverse comments are received",
                        "withdrawing this direct final rule",
                    ),
                )
                if not conditional_effect_clause:
                    reason_code = "CONDITIONAL_CLAUSE_MISSING"
            elif spec["kind"] == CORRECTION:
                corrected_date_state = spec["effective_day"]
                if not self._contains_all(normalized, ("correction", "effective date")):
                    reason_code = "CORRECTION_MARKER_MISSING"
            elif spec["kind"] == WITHDRAWAL:
                withdrawal_seen = True
                terminal_effective_day = spec["effective_day"]
                if not self._contains_all(
                    normalized,
                    ("withdrawing the direct final rule", "significant adverse comment"),
                ):
                    reason_code = "WITHDRAWAL_MARKER_MISSING"

        if not corrected_date_state and origin_seen:
            corrected_date_state = source_specs[0]["effective_day"]
        if not origin_seen or len(source_specs) < 2:
            identity_match = False
            reason_code = "INCOMPLETE_SOURCE_SET"
        if not identity_match or not conditional_effect_clause or reason_code:
            return json.dumps(
                {
                    "identity_match": identity_match,
                    "conditional_effect_clause": conditional_effect_clause,
                    "corrected_date_state": corrected_date_state,
                    "terminal_action": LINEAGE_WITHDRAWN if withdrawal_seen else "",
                    "terminal_effective_day": terminal_effective_day,
                    "status": LINEAGE_UNRESOLVED,
                    "rule_active": False,
                    "reason_code": reason_code or "EVIDENCE_RECONCILIATION_FAILED",
                    "evidence_fingerprint": hashlib.sha256("|".join(source_hashes).encode("utf-8")).hexdigest(),
                    "explanation": "Evidence identity, conditional clause, or source markers did not reconcile.",
                },
                sort_keys=True,
            )

        if withdrawal_seen:
            status = LINEAGE_WITHDRAWN
            rule_active = False
            reason_code = "SIGNIFICANT_ADVERSE_COMMENT"
            explanation = "Withdrawal is terminal and overrides every scheduled effective date."
        elif as_of_day < corrected_date_state:
            status = LINEAGE_PENDING
            rule_active = False
            reason_code = "EFFECTIVE_DATE_NOT_REACHED"
            explanation = "No withdrawal was found, but the corrected effective day has not arrived."
        else:
            status = LINEAGE_EFFECTIVE
            rule_active = True
            reason_code = "EFFECTIVE_DATE_REACHED"
            explanation = "Identity and conditional-effect evidence match and the corrected effective day has arrived."

        return json.dumps(
            {
                "identity_match": True,
                "conditional_effect_clause": True,
                "corrected_date_state": corrected_date_state,
                "terminal_action": LINEAGE_WITHDRAWN if withdrawal_seen else "NONE",
                "terminal_effective_day": terminal_effective_day,
                "status": status,
                "rule_active": rule_active,
                "reason_code": reason_code,
                "evidence_fingerprint": hashlib.sha256("|".join(source_hashes).encode("utf-8")).hexdigest(),
                "explanation": explanation,
            },
            sort_keys=True,
        )

    def _assert_owner(self) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("OWNER_ONLY")

    def _lineage_index(self, lineage_id: u256) -> int:
        for index in range(len(self.lineages)):
            if self.lineages[index].lineage_id == lineage_id:
                return index
        raise gl.vm.UserError("UNKNOWN_LINEAGE")

    def _assessment_index(self, lineage_id: u256, as_of_day: str) -> int:
        for index in range(len(self.assessments)):
            assessment = self.assessments[index]
            if assessment.lineage_id == lineage_id and assessment.as_of_day == as_of_day:
                return index
        return -1

    def _latest_assessment_index(self, lineage_id: u256) -> int:
        for index in range(len(self.assessments) - 1, -1, -1):
            if self.assessments[index].lineage_id == lineage_id:
                return index
        raise gl.vm.UserError("NO_ASSESSMENT")

    def _source_specs(self, lineage: Lineage) -> list[dict[str, str]]:
        specs: list[dict[str, str]] = []
        for instrument in self.instruments:
            if instrument.lineage_id == lineage.lineage_id:
                specs.append(
                    {
                        "agency": lineage.agency,
                        "docket": lineage.docket,
                        "rin": lineage.rin,
                        "originating_fr_doc": lineage.originating_fr_doc,
                        "originating_fr_citation": lineage.originating_fr_citation,
                        "expected_effective_day": lineage.expected_effective_day,
                        "kind": instrument.kind,
                        "fr_doc": instrument.fr_doc,
                        "url": instrument.url,
                        "source_hash": instrument.source_hash,
                        "effective_day": instrument.effective_day,
                    }
                )
        return specs

    @gl.public.write
    def create_lineage(
        self,
        agency: str,
        docket: str,
        rin: str,
        originating_fr_doc: str,
        originating_fr_citation: str,
        expected_effective_day: str,
    ) -> u256:
        self._assert_owner()
        if not all(
            self._is_safe_field(value)
            for value in (agency, docket, rin, originating_fr_doc, originating_fr_citation)
        ):
            raise gl.vm.UserError("IDENTITY_REQUIRED")
        if not self._is_iso_day(expected_effective_day):
            raise gl.vm.UserError("INVALID_EFFECTIVE_DAY")
        lineage_id = self.next_lineage_id
        self.lineages.append(
            Lineage(
                lineage_id,
                agency,
                docket,
                rin,
                originating_fr_doc,
                originating_fr_citation,
                expected_effective_day,
                LINEAGE_DRAFT,
                u256(0),
                u256(0),
                u256(0),
            )
        )
        self.next_lineage_id += u256(1)
        return lineage_id

    @gl.public.write
    def add_instrument(
        self,
        lineage_id: u256,
        kind: str,
        fr_doc: str,
        url: str,
        source_hash: str,
        retrieved_day: str,
        effective_day: str,
    ) -> None:
        self._assert_owner()
        lineage = self.lineages[self._lineage_index(lineage_id)]
        if lineage.state != LINEAGE_DRAFT:
            raise gl.vm.UserError("LINEAGE_NOT_DRAFT")
        if kind not in (ORIGINATING, CORRECTION, WITHDRAWAL):
            raise gl.vm.UserError("INVALID_INSTRUMENT_KIND")
        if lineage.instrument_count >= u256(3):
            raise gl.vm.UserError("INSTRUMENT_LIMIT")
        if not self._is_safe_field(fr_doc) or not url.startswith("https://www.federalregister.gov/"):
            raise gl.vm.UserError("OFFICIAL_SOURCE_REQUIRED")
        if not self._is_hex_digest(source_hash):
            raise gl.vm.UserError("INVALID_SOURCE_HASH")
        if not self._is_iso_day(retrieved_day) or not self._is_iso_day(effective_day):
            raise gl.vm.UserError("INVALID_SOURCE_DATE")
        if kind == ORIGINATING and fr_doc != lineage.originating_fr_doc:
            raise gl.vm.UserError("ORIGINATING_DOCUMENT_MISMATCH")
        if kind != WITHDRAWAL and effective_day != lineage.expected_effective_day:
            raise gl.vm.UserError("EXPECTED_EFFECTIVE_DAY_MISMATCH")
        for instrument in self.instruments:
            if instrument.lineage_id == lineage_id:
                if instrument.kind == kind or instrument.fr_doc == fr_doc or instrument.url == url:
                    raise gl.vm.UserError("DUPLICATE_INSTRUMENT")
        self.instruments.append(
            Instrument(lineage_id, kind, fr_doc, url, source_hash, retrieved_day, effective_day)
        )
        lineage.instrument_count += u256(1)

    @gl.public.write
    def seal_lineage(self, lineage_id: u256) -> None:
        self._assert_owner()
        lineage = self.lineages[self._lineage_index(lineage_id)]
        if lineage.state != LINEAGE_DRAFT:
            raise gl.vm.UserError("LINEAGE_NOT_DRAFT")
        kinds: list[str] = []
        for instrument in self.instruments:
            if instrument.lineage_id == lineage_id:
                kinds.append(instrument.kind)
        if len(kinds) < 2 or len(kinds) > 3 or ORIGINATING not in kinds:
            raise gl.vm.UserError("REQUIRE_TWO_OR_THREE_OFFICIAL_SOURCES")
        lineage.state = LINEAGE_PENDING

    @gl.public.write
    def assess_activation(self, lineage_id: u256, as_of_day: str) -> u256:
        lineage = self.lineages[self._lineage_index(lineage_id)]
        if lineage.state == LINEAGE_DRAFT or lineage.state == LINEAGE_SUPERSEDED:
            raise gl.vm.UserError("LINEAGE_NOT_ASSESSABLE")
        if not self._is_iso_day(as_of_day):
            raise gl.vm.UserError("INVALID_ASSESSMENT_DAY")
        replay_index = self._assessment_index(lineage_id, as_of_day)
        if replay_index >= 0:
            return self.assessments[replay_index].assessment_id
        if lineage.assessment_count > u256(0):
            latest_index = self._latest_assessment_index(lineage_id)
            if as_of_day < self.assessments[latest_index].as_of_day:
                raise gl.vm.UserError("ASSESSMENT_DAY_OUT_OF_ORDER")

        source_specs = self._source_specs(lineage)

        def fetch_and_derive() -> str:
            return self._fetch_and_derive(source_specs, as_of_day)

        result = gl.eq_principle.strict_eq(fetch_and_derive)
        data = json.loads(result)
        assessment_id = self.next_assessment_id
        self.assessments.append(
            Assessment(
                assessment_id,
                lineage_id,
                as_of_day,
                data["status"],
                data["identity_match"],
                data["conditional_effect_clause"],
                data["corrected_date_state"],
                data["terminal_action"],
                data["terminal_effective_day"],
                data["rule_active"],
                data["reason_code"],
                data["evidence_fingerprint"],
                data["explanation"],
            )
        )
        self.next_assessment_id += u256(1)
        lineage.assessment_count += u256(1)
        lineage.state = data["status"]
        return assessment_id

    @gl.public.write
    def supersede_lineage(self, lineage_id: u256, replacement_lineage_id: u256) -> None:
        self._assert_owner()
        if lineage_id == replacement_lineage_id:
            raise gl.vm.UserError("INVALID_REPLACEMENT")
        lineage = self.lineages[self._lineage_index(lineage_id)]
        replacement = self.lineages[self._lineage_index(replacement_lineage_id)]
        if lineage.state == LINEAGE_DRAFT or replacement.state in (LINEAGE_DRAFT, LINEAGE_SUPERSEDED):
            raise gl.vm.UserError("LINEAGE_NOT_SUPERSEDABLE")
        if lineage.state == LINEAGE_SUPERSEDED:
            raise gl.vm.UserError("ALREADY_SUPERSEDED")
        if (
            lineage.agency != replacement.agency
            or lineage.docket != replacement.docket
            or lineage.rin != replacement.rin
            or lineage.originating_fr_doc != replacement.originating_fr_doc
            or lineage.originating_fr_citation != replacement.originating_fr_citation
        ):
            raise gl.vm.UserError("LINEAGE_IDENTITY_MISMATCH")
        lineage.state = LINEAGE_SUPERSEDED
        lineage.superseded_by = replacement_lineage_id

    @gl.public.view
    def get_lineage(self, lineage_id: u256) -> dict[str, str]:
        lineage = self.lineages[self._lineage_index(lineage_id)]
        return {
            "lineage_id": str(lineage.lineage_id),
            "agency": lineage.agency,
            "docket": lineage.docket,
            "rin": lineage.rin,
            "originating_fr_doc": lineage.originating_fr_doc,
            "originating_fr_citation": lineage.originating_fr_citation,
            "expected_effective_day": lineage.expected_effective_day,
            "state": lineage.state,
            "instrument_count": str(lineage.instrument_count),
            "assessment_count": str(lineage.assessment_count),
            "superseded_by": str(lineage.superseded_by),
        }

    @gl.public.view
    def get_instrument(self, lineage_id: u256, source_index: u256) -> dict[str, str]:
        seen = u256(0)
        for instrument in self.instruments:
            if instrument.lineage_id == lineage_id:
                if seen == source_index:
                    return {
                        "lineage_id": str(instrument.lineage_id),
                        "kind": instrument.kind,
                        "fr_doc": instrument.fr_doc,
                        "url": instrument.url,
                        "source_hash": instrument.source_hash,
                        "retrieved_day": instrument.retrieved_day,
                        "effective_day": instrument.effective_day,
                    }
                seen += u256(1)
        raise gl.vm.UserError("UNKNOWN_INSTRUMENT")

    @gl.public.view
    def get_assessment(self, lineage_id: u256) -> dict[str, str]:
        assessment = self.assessments[self._latest_assessment_index(lineage_id)]
        return {
            "assessment_id": str(assessment.assessment_id),
            "lineage_id": str(assessment.lineage_id),
            "as_of_day": assessment.as_of_day,
            "status": assessment.status,
            "identity_match": str(assessment.identity_match),
            "conditional_effect_clause": str(assessment.conditional_effect_clause),
            "corrected_date_state": assessment.corrected_date_state,
            "terminal_action": assessment.terminal_action,
            "terminal_effective_day": assessment.terminal_effective_day,
            "rule_active": str(assessment.rule_active),
            "reason_code": assessment.reason_code,
            "evidence_fingerprint": assessment.evidence_fingerprint,
            "explanation": assessment.explanation,
        }

    @gl.public.view
    def is_rule_active(self, lineage_id: u256) -> bool:
        lineage = self.lineages[self._lineage_index(lineage_id)]
        return lineage.state == LINEAGE_EFFECTIVE
