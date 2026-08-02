# SALI

SALI (Service Authorization and Licensing Interface) is an entirely
fictional system operated by the fictional Northbridge Department of
Professional Services. It does not describe a real organization, customer,
system, or production environment.

SALI is a Python and JavaScript application running in managed containers
with a managed relational database and object storage.

## Monitoring

SALI uses its hosting provider's native infrastructure monitoring and
alerting tools. Application, identity, container, database, and platform
audit logs are forwarded to Northbridge Central Monitor, a fictional SIEM.
A separate fictional application-performance service collects transaction
traces and service-health metrics.

The security team reviews critical alerts continuously and lower-priority
alerts each business day. Security advisories are reviewed within one
business day and assigned to the SALI product owner for impact analysis.

## Web attack prevention

The SALI web application is fronted by a managed load balancer and web
application firewall. The firewall uses the OWASP Core Rule Set plus
application-specific rules maintained by the platform team. Rate limiting,
input validation, and file-upload restrictions are also enforced in the
application.

The hosting provider supplies network-layer denial-of-service protection.
The SALI team can adjust application rate limits and firewall rules but
cannot alter the provider's underlying network controls.

## General architecture

- Public users access SALI through a browser-based licensing portal.
- Application services run in managed containers across two availability
  zones.
- A managed relational database stores licensing and workflow records.
- Uploaded supporting documents are stored in encrypted object storage.
- Development, test, and production use separate accounts and credentials.

## Identity, Authentication, and Access

- Public users authenticate through Northbridge Citizen Login, a fictional
  identity service.
- Workforce users authenticate through the fictional Northbridge Workforce
  Identity service using multifactor authentication.
- Administrative access requires a separate privileged role.
- Access is reviewed quarterly and when personnel change roles.

## Organizational Structure and Separation of Duties

- The product owner approves production access and accepts system risk.
- The application team develops and maintains SALI.
- The platform team administers container, database, and storage services.
- The security team monitors alerts and investigates incidents.
- Developers cannot approve their own production deployments.
- Audit administrators cannot modify licensing records.

## Least Functionality Posture

- Access is denied by default and allowed only by documented exception.
- Only approved ports, services, packages, and container images are
  permitted in production.
- Administrative interfaces are restricted to the management network.
- Exceptions require approval, a business justification, and an expiration
  date.

## Canonical Decisions and Definitions

- SALI may store fictional moderate-impact licensing information.
- Payment-card data is handled by a separate fictional payment provider and
  is not stored in SALI.
- The security team owns alert triage; the application team owns remediation.

## Completed Control Response example outlines

- IA-2 - Identification and Authentication: separate fictional identity
  services for public and workforce users, multifactor authentication for
  workforce access, and role-based authorization.
- AC-5 - Separation of Duties: product, development, platform, and security
  responsibilities are separated, including deployment approval and audit
  administration.
