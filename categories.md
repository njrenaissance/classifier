<!--
  Category definitions — Discovery evidence classifier (TEST BATCH: 10 categories).

  version: 1.0.0

  Source: Revised_Full_Checklist_Rev_07_11.docx, "Short Form" section. Per
  instruction, each category here is an individual checkbox/line item from the
  Short Form tables (NOT a table header) — the DA Designations table is
  excluded entirely. Each category's description notes which Short Form
  section it was drawn from as a "super-category" reference only; that
  grouping does not drive classification.

  Format (parsed by src/categories.py):
    - `## Name`            a category, ordered by appearance
    - prose before bullets  the category's description (optional)
    - `-`/`*`/`+` bullets    few-shot examples (at least one required per category)
    - `## unknown`          reserved fallback bucket; never a real category
-->

# Document categories

## Arrest Report
The initial police report memorializing the circumstances of an arrest, whether generated through the Omniform system or completed as a handwritten worksheet. (Short Form section: Discovery that should exist.)

- Omniform-generated arrest report listing charges, time, and location of arrest
- Handwritten arrest report worksheet completed by the arresting officer
- Narrative report documenting how and why an arrest was made

## Complaint Report
The department's official complaint record (UF-61) documenting a reported crime, and its handwritten worksheet counterpart ("Scratch 61"). (Short Form section: Discovery that should exist.)

- Omniform UF-61 complaint report describing the reported offense
- Handwritten "Scratch 61" complaint worksheet filled out at the scene
- Initial incident report opening a criminal complaint

## Arraignment Card
The court-verification card confirming a defendant's arraignment date, charges, and processing information. (Short Form section: Discovery that should exist.)

- NYPD arraignment card noting charges filed and the arraignment date
- Court verification slip confirming a defendant's appearance for arraignment

## BWC Checklist
The completed checklist tracking which officers wore body-worn cameras during the incident and confirming their footage was captured. (Short Form section: Discovery that should exist.)

- Checklist listing each responding officer's BWC activation status
- Form confirming which officers had body cameras running during the arrest

## Activity Logs
Officers' and detectives' daily activity logs (memo books) recording their actions, assignments, and observations during a tour. (Short Form section: Discovery that should exist.)

- Officer's memo book entries covering the date of the arrest
- Detective's activity log noting steps taken during the investigation

## ECMS Access Log
The audit trail showing which users accessed or modified a case record in the Electronic Case Management System. (Short Form section: Discovery that May Exist.)

- System-generated log of ECMS logins and edits tied to the case file
- Audit trail showing which detectives accessed the electronic case record

## BWC Metadata
Technical metadata accompanying body-worn camera footage, such as timestamps, device ID, and activation events. (Short Form section: Discovery that should exist.)

- Metadata file listing timestamps and device IDs for BWC footage
- System export showing when a body camera was turned on and off

## Command Log
The precinct command log recording notable events, assignments, and occurrences during a given tour. (Short Form section: Discovery that should exist.)

- Precinct command log entry referencing the arrest or incident
- Desk officer's log of events and assignments during the shift

## CCRB History Report
A Civilian Complaint Review Board report summarizing an officer's history of civilian complaints, relevant to Giglio disclosure obligations. (Short Form section: Giglio.)

- CCRB history report listing prior complaints against a testifying officer
- Summary of an officer's civilian complaint record used for impeachment

## Search Warrant
The judicially authorized warrant permitting a search of a person, place, or device, along with its supporting application materials. (Short Form section: Search Warrants.)

- Signed search warrant authorizing a search of a specific location or device
- Judicial order granting police permission to search based on probable cause

## unknown
Reserved fallback bucket for documents that fit none of the categories above.
The classifier emits ``unknown`` rather than forcing a poor match; this section
defines no examples and is never treated as a real category.
