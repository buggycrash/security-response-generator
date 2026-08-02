# DEMO-ECMS

DEMO-ECMS (Demonstration Example Case Management System) is a fictional
Python and JavaScript application running in managed containers in a
commercial cloud environment. It is operated by the fictional Department of
Community Services. This file is demonstration material and does not
describe a real customer or production system.

## Monitoring

DEMO-ECMS uses the cloud provider's native infrastructure monitoring and
alerting services. Application, container, identity, database, and cloud
audit logs are forwarded to Example Sentinel, the department's fictional
centralized SIEM. Example APM provides application performance monitoring
and transaction tracing for DEMO-ECMS.

The security operations team continuously monitors high-priority alerts and
reviews lower-priority alerts each business day. Security advisories are
reviewed within one business day. Relevant advisories are assigned to the
DEMO-ECMS system owner and engineering lead for impact analysis,
remediation, and closure tracking.

## Web attack prevention

The DEMO-ECMS public web application is protected by a managed web application
firewall using the OWASP Core Rule Set and organization-specific rules.
Requests pass through a managed load balancer before reaching the
application containers. The firewall blocks common injection, cross-site
scripting, malicious file-upload, and known hostile IP patterns.

The cloud provider supplies network-layer distributed denial-of-service
protection. The application team can adjust rate limits and web firewall
rules but cannot change the provider's underlying network protection.

## General architecture

- Public users access DEMO-ECMS through a browser-based case-management
  application.
- Stateless application services run in managed containers across two
  availability zones.
- A managed relational database stores case and workflow data.
- Documents are stored in encrypted object storage.
- Development, test, and production environments use separate accounts and
  credentials.
- Production changes are deployed through an approved automated pipeline.

## Identity, Authentication, and Access

- Public users authenticate through Example Login, a fictional external
  identity service.
- Workforce users authenticate through the enterprise identity provider
  using phishing-resistant multifactor authentication.
- Administrative access requires a separate privileged role.
- Role-based access control limits users to assigned job functions.
- Access reviews occur quarterly and when personnel change roles.
- Authentication, authorization, and administrative events are logged.

## Organizational Structure and Separation of Duties

- The DEMO-ECMS system owner approves production access and accepts system
  risk.
- The engineering team develops and maintains application code.
- The platform team administers the container and database services.
- The security operations team monitors alerts and investigates incidents.
- Developers cannot approve their own production deployments.
- Audit-log administrators cannot alter application business records.

## Least Functionality Posture

- Deny by default and allow by documented exception.
- Only approved network ports, services, packages, and container images are
  permitted in production.
- Administrative interfaces are restricted to the management network.
- Unused accounts, sample applications, and default services are disabled.
- Exceptions require documented business justification, approval, and an
  expiration date.

## Canonical Decisions and Definitions

- DEMO-ECMS may store fictional moderate-impact case information but may
  not store classified information or payment card data.
- A high-priority security alert requires immediate triage.
- A lower-priority alert must be reviewed by the end of the next business
  day.
- The DEMO-ECMS system owner is accountable for control implementation;
  operational tasks may be delegated to the platform, engineering, or
  security teams.
- Example Sentinel is the authoritative repository for DEMO-ECMS security
  logs.

## Completed Control Response example outlines

- IA-2 - Identification and Authentication: Example Login for public users,
  enterprise identity for workforce users, phishing-resistant MFA for
  workforce and administrators, and role-based access control.
- AC-5 - Separation of Duties: system-owner approval, independent deployment
  approval, separate platform and security operations responsibilities, and
  restricted audit-log administration.
- SI-5 - Security Alerts, Advisories, and Directives: security operations
  reviews advisories within one business day, assigns relevant items for
  impact analysis and remediation, and tracks them through closure.
