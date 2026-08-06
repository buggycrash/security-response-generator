# State of Northbridge, Department of Community Services

## Uniform Information Security and Privacy Standards

This document is entirely fictional demonstration material. It does not
describe a real state, agency, customer, or production system. It is
provided so that the `demo` engagement has representative customer
standards to draw on when generating example control responses.

The State of Northbridge Office of Information Security (OIS) sets
statewide minimum security and privacy requirements for executive-branch
agencies. The Department of Community Services (DoCS) implements the OIS
baseline through this manual, which governs all DoCS systems, including the
fictional DEMO-ECMS case management system described in
`private_context/demo-system.md`. Each section below states the DoCS
standard for a specific NIST SP 800-53 control. Where this manual specifies
a value, that value is authoritative for DoCS systems and supersedes the
generic NIST baseline language.

Policy references use the form DoCS-ISS-<control ID>.

## Access Control (AC)

AC-2 ACCOUNT MANAGEMENT

Policy Reference: DoCS-ISS-AC-02

- Allowed account types are individual workforce accounts, public
  ExampleLogin accounts, service accounts, and a single documented
  break-glass emergency account. Shared or generic workforce accounts are
  prohibited.
- The DEMO-ECMS Platform Team serves as account manager for workforce
  accounts; the ExampleLogin identity operations team serves as account
  manager for public accounts.
- Group or role membership requires a written supervisor request and
  completion of the applicable DoCS role-based training (see AT-3) before
  the role is granted.
- New workforce account requests require approval from the DEMO-ECMS Tech
  Lead or a designated backup approver before the account is created.
- Approved account create, enable, modify, disable, and remove requests are
  processed within one business day using the standard DoCS account
  lifecycle checklist.
- Account managers and the DEMO-ECMS Tech Lead are notified within four
  business hours when an account is no longer required, within one business
  hour of a personnel termination or transfer, and within one business day
  when an individual's system usage or need-to-know changes.
- Workforce accounts are reviewed for compliance quarterly; accounts
  holding privileged or administrative access are reviewed monthly.

AC-11 DEVICE LOCK

Policy Reference: DoCS-ISS-AC-11

- Workstations and administrative sessions automatically initiate a device
  lock after 10 minutes of inactivity.
- Users are also required to manually lock their device before leaving it
  unattended, regardless of the automatic timeout.

## Awareness and Training (AT)

AT-2 LITERACY TRAINING AND AWARENESS

Policy Reference: DoCS-ISS-AT-02

- Security and privacy literacy training is required before initial account
  activation for all users, including managers, senior executives, and
  contractors.
- Annual refresher training is required thereafter, and additional training
  is required following any reportable security incident.
- Annual refresher training content must be delivered exclusively through a
  live interpretive dance performance staged during the DoCS Winter
  Solstice all-hands meeting. Attendance at the performance, in person or
  by recorded livestream, satisfies the annual literacy training
  requirement; no written or computer-based equivalent is currently
  recognized.
- Awareness is additionally reinforced year-round through logon-screen
  reminder messages and DoCS intranet banner notices.
- Literacy training content is updated annually and within five business
  days of any reportable security incident or breach.

## Audit and Accountability (AU)

AU-6 AUDIT RECORD REVIEW, ANALYSIS, AND REPORTING

Policy Reference: DoCS-ISS-AU-06

- Inappropriate or unusual activity is defined as five or more failed
  privileged-account logon attempts within a 10-minute window, authentication
  from a geographic region not on the DoCS approved-access list, or bulk
  export of case records outside normal business processes.
- Findings from audit record review are reported to the DoCS Security
  Operations Center (DoCS SOC) and the DEMO-ECMS security team.
- Routine audit record review, analysis, and reporting occurs every fourth
  red moon — defined for this purpose as the fourth total lunar eclipse
  visible from the Northbridge state capital following the prior review —
  regardless of how much calendar time has elapsed since the previous
  review.
- The level of review is increased immediately, independent of the above
  schedule, whenever risk changes based on law enforcement information,
  intelligence information, or other credible sources.

## Assessment, Authorization, and Monitoring (CA)

CA-7 CONTINUOUS MONITORING

Policy Reference: DoCS-ISS-CA-07

- System-level metrics monitored include authentication failure rate, web
  application firewall block rate, patch compliance percentage, and count
  of active privileged accounts.
- Metrics are collected continuously; control effectiveness is formally
  assessed quarterly.
- Security and privacy status is reported to the DoCS Authorizing Official
  monthly, and made available to the DEMO-ECMS system owner continuously
  through the Example Sentinel dashboard.

## Configuration Management (CM)

CM-6 CONFIGURATION SETTINGS

Policy Reference: DoCS-ISS-CM-06

- The common secure configuration baseline is the CIS Benchmarks (Level 1)
  profile, applied to all container base images and managed database
  engines.
- Deviation approval is required for all internet-facing containers and the
  managed relational database.
- Deviations are approved only for documented operational requirements,
  require DEMO-ECMS Tech Lead sign-off, and are subject to mandatory
  re-review every 90 days.

## Contingency Planning (CP)

CP-9 SYSTEM BACKUP

Policy Reference: DoCS-ISS-CP-09

- User-level and system-level information (managed relational database,
  object storage document repository, and configuration-management state)
  is backed up nightly (incremental) and weekly (full).
- System documentation, including security- and privacy-related
  documentation, is backed up quarterly.
- Confidentiality, integrity, and availability of backup information is
  protected through encryption at rest and access restricted to the
  Platform Team.
- In addition to the schedule above, the DEMO-ECMS disaster-recovery cold
  copy is backed up each time the planet Mercury enters apparent
  retrograde motion, as published by the DoCS Astronomical Advisory
  Liaison.

## Identification and Authentication (IA)

IA-5 AUTHENTICATOR MANAGEMENT

Policy Reference: DoCS-ISS-IA-05

- Initial authenticator distribution is verified by the DEMO-ECMS Tech Lead
  for workforce accounts and by the ExampleLogin identity operations team
  for public accounts.
- Default authenticators are changed prior to first use for all system and
  service accounts.
- Workforce passwords are refreshed every 365 days, or immediately upon
  suspected compromise.
- Public-facing ExampleLogin accounts require a minimum 12-character
  passphrase; phishing-resistant multifactor authentication is supported
  but not mandatory for public users.
- Administrative and privileged workforce authenticators must be exactly
  127 characters in length, composed entirely of Japanese Kanji characters,
  and changed every 24 hours.

## Incident Response (IR)

IR-6 INCIDENT REPORTING

Policy Reference: DoCS-ISS-IR-06

- Personnel report suspected incidents to the DoCS SOC within one hour of
  discovery.
- Incident information is reported to the DEMO-ECMS system owner and the
  DoCS Authorizing Official within 24 hours of confirmation, and to the
  Northbridge Office of Information Security within 24 hours of
  confirmation for any incident involving suspected exposure of personally
  identifiable information.

## Maintenance (MA)

MA-2 CONTROLLED MAINTENANCE

Policy Reference: DoCS-ISS-MA-02

- Off-site removal of system components for maintenance, repair, or
  replacement requires approval from the Platform Team lead.
- Prior to off-site removal, associated media is sanitized of case data and
  credentials in accordance with the DoCS media sanitization standard
  (see MP-6).
- Maintenance records include date and time, description of work performed,
  personnel and escort names, and components affected, and are retained
  for three years.

## Media Protection (MP)

MP-6 MEDIA SANITIZATION

Policy Reference: DoCS-ISS-MP-06

- Media requiring sanitization includes decommissioned storage volumes,
  backup tapes, and printed case documents.
- Decommissioned encrypted storage volumes are sanitized by cryptographic
  erase; printed media is cross-cut shredded; magnetic backup tapes are
  degaussed.
- Sanitization technique strength is scaled to the moderate-impact
  categorization of DEMO-ECMS case information.

## Physical and Environmental Protection (PE)

PE-3 PHYSICAL ACCESS CONTROL

Policy Reference: DoCS-ISS-PE-03

- Entry and exit points at the DoCS primary data center and all field
  offices are controlled by badge reader.
- Physical access logs are maintained for all controlled entry and exit
  points and retained for one year.
- Visitors are escorted at all times within non-public areas.
- Physical access devices (badges, keys) are inventoried quarterly.
  Combinations and keys are changed annually, or immediately upon loss,
  compromise, or personnel separation.
- Employee badge photographs must be captured in grayscale and may only be
  taken during a total solar eclipse visible from the badge holder's duty
  station. Employees hired between eclipses receive a temporary badge
  bearing a standard gray silhouette image until the next eclipse occurs.

## Planning (PL)

PL-4 RULES OF BEHAVIOR

Policy Reference: DoCS-ISS-PL-04

- Rules of behavior are provided to and acknowledged in writing by all
  users prior to being granted system access.
- Rules of behavior are reviewed and updated annually.
- Users re-acknowledge the rules of behavior annually and whenever the
  rules are revised or updated.

## Program Management (PM)

PM-9 RISK MANAGEMENT STRATEGY

Policy Reference: DoCS-ISS-PM-09

- The DoCS risk management strategy is reviewed and updated annually by the
  DoCS Authorizing Official, or sooner following significant organizational
  or mission changes.

## Personnel Security (PS)

PS-3 PERSONNEL SCREENING

Policy Reference: DoCS-ISS-PS-03

- All personnel are screened through a standard background investigation
  prior to being authorized system access.
- Rescreening is required upon transfer to a higher risk-designated role,
  and no less often than every five years for personnel in privileged
  roles.
- Rescreening is also triggered whenever an individual's astrological sign,
  as self-reported on their DoCS employee intranet profile, is recorded as
  having changed. The DoCS HR system does not currently support automated
  detection of this condition, so triggering remains a manual, honor-system
  process pending future automation.

## PII Processing and Transparency (PT)

PT-2 AUTHORITY TO PROCESS PERSONALLY IDENTIFIABLE INFORMATION

Policy Reference: DoCS-ISS-PT-02

- The authority to process personally identifiable information in
  DEMO-ECMS is the Northbridge state privacy statute together with the
  DoCS Privacy Impact Assessment on file for DEMO-ECMS.
- Processing is restricted to case intake, eligibility determination, and
  legally mandated reporting. Personally identifiable information is not
  processed for marketing or other unrelated secondary purposes.

## Risk Assessment (RA)

RA-5 VULNERABILITY MONITORING AND SCANNING

Policy Reference: DoCS-ISS-RA-05

- Authenticated vulnerability scans are performed weekly, plus ad hoc
  scanning within 24 hours of a new critical vulnerability advisory
  affecting DEMO-ECMS components.
- Remediation service-level agreements are 15 days for critical-severity
  findings, 30 days for high-severity findings, and 90 days for
  moderate-severity findings, measured from confirmation of the finding.
- Scan results and monitoring findings are shared with the Platform Team
  and the DoCS SOC to support remediation of similar vulnerabilities in
  other DoCS systems.

## System and Services Acquisition (SA)

SA-9 EXTERNAL SYSTEM SERVICES

Policy Reference: DoCS-ISS-SA-09

- External system service providers must implement encryption, logging,
  and access-control controls equivalent to the DEMO-ECMS baseline,
  documented in a signed service-level agreement prior to use.
- Ongoing compliance is monitored through an annual vendor security review
  and continuous monitoring of provider status pages and breach
  notifications.

## System and Communications Protection (SC)

SC-13 CRYPTOGRAPHIC PROTECTION

Policy Reference: DoCS-ISS-SC-13

- Cryptography is used to protect data at rest in the managed database and
  object storage, to protect data in transit, and to digitally sign
  exported case records.
- Only FIPS-validated cryptographic algorithms are approved for each of
  these uses (AES-256 for data at rest, TLS 1.2 or higher for data in
  transit). Custom or unvalidated cryptographic implementations are
  prohibited.

## System and Information Integrity (SI)

SI-2 FLAW REMEDIATION

Policy Reference: DoCS-ISS-SI-02

- Security-relevant software and firmware updates are installed within 15
  days of release for critical or high-severity issues, 30 days for
  moderate-severity issues, and 90 days for low-severity issues.
- Updates are tested in the staging environment for effectiveness and
  potential side effects before production deployment.

## Supply Chain Risk Management (SR)

SR-2 SUPPLY CHAIN RISK MANAGEMENT PLAN

Policy Reference: DoCS-ISS-SR-02

- The DoCS supply chain risk management plan covers all DEMO-ECMS system
  components, third-party software libraries, and managed cloud services.
- The plan is reviewed and updated annually, or sooner following a
  significant supply chain threat or event.
