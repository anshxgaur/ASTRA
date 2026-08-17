"""
09_EMBEDDINGS (part 1) — turn a raw contextual field + its surrounding
structured facts into a rich sentence BEFORE embedding it (design doc
Section 6: never embed the raw remark alone). Every chunk ends with a
[Source: ...] citation so answers stay traceable.
"""


def build_internship_context(row) -> str:
    """AICTE internship-portal row (clean 6th source)."""
    stipend = row.get("stipend_amount")
    stipend_text = ""
    if stipend not in (None, "") and float(stipend) > 0:
        stipend_text = f" with a stipend of Rs {float(stipend):,.0f}"
    elif stipend not in (None, ""):
        stipend_text = " (unpaid)"
    ppo = " with a PPO possibility" if row.get("is_ppo_linked") else ""
    text = (f"An internship in {row['domain']} is available at "
            f"{row['institution_name']} through {row.get('organization_name') or 'an organization'}"
            f" ({row.get('program_source') or 'General'} program, "
            f"{row.get('duration_weeks') or 'n/a'} weeks, {row.get('mode') or 'n/a'} mode)"
            f"{stipend_text}{ppo}. {row.get('description') or ''}".strip())
    return _cite(text, row)


def _approval_text(value):
    if value is True:
        return "Approved"
    if value is False:
        return "Rejected"
    if value is None or value == "" or (isinstance(value, float) and value != value):
        return "Under Review / unknown"
    return str(value)


def build_institution_context(row) -> str:
    bits = [f"{row['institution_name']} is a {row.get('institute_type') or 'n/a'} "
            f"institute in {row.get('district') or 'n/a'}, {row.get('state') or 'n/a'}"]
    if row.get("year_established"):
        bits.append(f"established in {row['year_established']}")
    if row.get("aicte_code"):
        bits.append(f"AICTE code {row['aicte_code']}")
    bits.append(f"approval status is {_approval_text(row.get('approval_status'))}")
    status = row.get("current_status")
    if status:
        bits.append(f"current status: {status}")
    if row.get("ownership"):
        bits.append(f"{row['ownership'].lower()} institution")
    if row.get("is_autonomous"):
        bits.append("autonomous institute")
    if row.get("nba_accredited"):
        valid = row.get("accreditation_valid_until") or "unknown"
        bits.append(f"NBA accredited (valid until {valid})")
    text = ". ".join(bits) + "."
    return _cite(text, row)


def build_course_context(row) -> str:
    fee = row.get("fee_per_year")
    fee_text = f" with an annual fee of Rs {float(fee):,.0f}" if fee not in (None, "") else ""
    status = row.get("course_status")
    status_text = f" Course status: {status}." if status else ""
    text = (f"{row['institution_name']} offers {row['course_name']}"
            f" (department: {row.get('department') or 'n/a'}, "
            f"{row.get('duration_years') or 'n/a'} years, "
            f"intake {row.get('intake_capacity') or 'n/a'}){fee_text}.{status_text}")
    return _cite(text, row)


def build_faculty_context(row) -> str:
    text = (f"{row['faculty_name']} is a {row.get('designation') or 'faculty member'} "
            f"({row.get('qualification') or 'n/a'}) in the {row.get('department') or 'n/a'} "
            f"department at {row['institution_name']}.")
    if row.get("specialization"):
        text += f" Specialization: {row['specialization']}."
    if row.get("years_of_experience"):
        text += f" {row['years_of_experience']} years of experience."
    return _cite(text, row)


def build_scholarship_context(row) -> str:
    text = (f"The scholarship scheme '{row['scheme_name']}', administered by "
            f"{row.get('administering_body') or 'n/a'}, offers {row.get('amount') or 'n/a'}"
            f"{' and is applicable in: ' + row['applicable_states'] if row.get('applicable_states') else ''}."
            f" Eligibility: {row.get('eligibility') or 'not specified'}.")
    return _cite(text, row)


def build_approval_context(row) -> str:
    atype = row.get("approval_type") or "n/a"
    if atype == "nba":
        detail = (f"NBA status is {row.get('nba_status') or 'n/a'}"
                  f" (valid until {row.get('valid_until') or 'unknown'})")
    elif atype == "closed":
        detail = f"closed in {row.get('closure_year') or 'unknown'}"
    else:
        detail = f"listed on the unapproved register (reason: {row.get('reason') or 'n/a'})"
    text = f"{row['institution_name']}: {detail}."
    return _cite(text, row)


def _cite(text: str, row) -> str:
    src = row.get("source_system") or row.get("source_database") or "unknown"
    rid = row.get("source_record_id", "")
    return f"{text} [Source: {src}, record {rid}]"


if __name__ == "__main__":
    import pandas as pd
    print(build_internship_context(pd.Series({
        "domain": "Data Science", "institution_name": "IIT Delhi",
        "organization_name": "Google India", "program_source": "General",
        "duration_weeks": 8, "mode": "online", "stipend_amount": 12000,
        "is_ppo_linked": True, "description": "Build and ship a real feature.",
        "source_system": "internships", "source_record_id": "1",
    })))
