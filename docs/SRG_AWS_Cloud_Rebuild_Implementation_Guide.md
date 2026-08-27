# Security Response Generator — AWS Cloud Rebuild Implementation Guide

**Status:** MVP design guide  
**Prepared:** August 26, 2026  
**Assumed region:** us-east-1 unless otherwise noted  
**Intended deployment model:** one AWS environment, one primary user, one engagement  
**Source basis:** the public `security-response-generator` README and `docs/technical-readme.md`, plus the architecture decisions developed during planning. The existing application code was intentionally not analyzed.

---

## 1. Purpose

Rebuild the core capability of Security Response Generator (SRG) as an AWS-native application managed by Terraform.

The cloud version does **not** need feature-for-feature parity with the existing laptop application. It should preserve the design intent:

- help a non-compliance-specialist engineer draft credible security control responses;
- ground drafts in authoritative source material rather than model memory;
- prefer engagement/customer-specific requirements over generic baseline language;
- validate control identifiers deterministically outside the LLM;
- make missing facts visible rather than hallucinating them;
- keep the user in the review loop;
- support a second-model review pass;
- keep deployment and operations simple enough for a small team or a single engineer.

The economic goal is not “free AI.” The goal is to be dramatically cheaper than hiring a compliance specialist to manually draft the same material.

---

## 2. Key Architecture Decisions

| Area | MVP decision | Rationale |
|---|---|---|
| Infrastructure as code | Terraform | Reproducible customer deployments and environment ownership |
| Generator model | NVIDIA Nemotron 3 Nano 30B A3B on Amazon Bedrock | Very low token cost with stronger expected reasoning than SRG's default Gemma 4 E4B |
| Reviewer model | Google Gemma 4 E2B on Amazon Bedrock | Cheap, different model family, suitable for independent review |
| Retrieval | Amazon Bedrock Knowledge Bases + Amazon S3 Vectors | Managed RAG without OpenSearch fixed cost |
| Source storage | Amazon S3 | Cheap, durable, versionable |
| Control catalog/state | Amazon DynamoDB | Serverless, cheap, deterministic lookups |
| Workflow | AWS Step Functions | Explicit orchestration and direct AWS service integrations |
| Application code | One ECS/Fargate worker container | One runtime/dependency/SBOM lifecycle instead of many Lambdas |
| Worker mode | ECS service, normally one warm task | Avoid per-request Fargate cold starts |
| GUI | Static SPA behind CloudFront | No persistent web server required |
| Authentication | Amazon Cognito | Managed user auth; one-user MVP now, extensible later |
| Job handoff | SQS or Step Functions job state | Decouples browser/API from long generation times |
| Container registry | Amazon ECR | Central image lifecycle; Inspector/SBOM scanning can be enabled |
| CI/CD | None initially | Terraform and builds run from developer laptop |
| Customer isolation | One engagement per AWS environment | Strongest isolation and simplest mental model |

---

## 3. Design Principle: Deterministic Shell, Probabilistic Core

The LLM should write and reason. It should **not** be trusted with decisions the application can make deterministically.

Application logic should decide:

- whether a requested NIST control exists;
- which engagement is active;
- which source tier a retrieved document belongs to;
- whether an exact customer-specific control mapping exists;
- which model is generator vs reviewer;
- whether required structured fields are present;
- whether a generated result can be persisted as complete.

The model may decide:

- how to synthesize grounded evidence into readable prose;
- whether the supplied facts appear insufficient;
- what follow-up question would be most useful;
- how to improve a draft after reviewer feedback;
- what evidence an assessor may reasonably expect.

A useful mental model is:

```text
deterministic validation
        ↓
authority-aware retrieval
        ↓
grounded model generation
        ↓
structured validation
        ↓
independent review
        ↓
optional revision
        ↓
human review
```

---

## 4. Target Architecture

```text
                          Browser
                             │
                             ▼
                        CloudFront
                       /          \
                      /            \
              Static SPA         API Gateway
                                   │
                                Cognito
                                   │
                                   ▼
                            Step Functions
                       ┌───────────┼───────────┐
                       │           │           │
                    DynamoDB       S3         SQS
                       │           │           │
                       │           │           ▼
                       │           │    ECS/Fargate worker
                       │           │      desired_count=1
                       │           │           │
                       │           │           ├── prompt assembly
                       │           │           ├── validation
                       │           │           └── result processing
                       │           │
                       └───────────┼─────────────────────┐
                                   │                     │
                                   ▼                     ▼
                           Bedrock Knowledge Base    Bedrock inference
                                   │                 ├── Nemotron generator
                                   ▼                 └── Gemma reviewer
                              S3 Vectors
```

### What is deliberately absent

- EC2 instances
- Kubernetes/EKS
- RDS
- OpenSearch Serverless
- NAT Gateway in the personal dev profile
- ALB for the worker
- many independent Lambda functions
- always-on SageMaker endpoints
- a GitHub CI/CD pipeline for the MVP

---

## 5. Authority and Retrieval Model

Preserve the original SRG source precedence:

1. **Canonical baseline** — NIST control text and related baseline material.
2. **Engagement/customer standard** — authoritative engagement-specific requirements; these override generic baseline wording where applicable.
3. **Private system context** — architecture, procedures, implementation facts, operational notes.
4. **Analyst/user notes** — request-specific facts entered while drafting.

The prompt should keep these tiers visibly separate.

Example grounding packet:

```text
[CANONICAL NIST CONTROL]
...

[ENGAGEMENT-SPECIFIC AUTHORITATIVE STANDARD]
...

[PRIVATE SYSTEM FACTS]
...

[ANALYST-SUPPLIED FACTS]
...
```

The application, not the model, sets the source tier.

### Recommended vector metadata

```text
tier             = "baseline" | "customer_standard" | "private_context"
control_ids      = ["AC-2", "AC-2(1)"]
source_document  = "security-standard.pdf"
source_version   = "2026-04"
engagement_id    = "local-environment-engagement"
```

Because the MVP has one engagement per AWS environment, cross-customer filtering is not a security boundary. The AWS environment is the boundary.

---

## 6. Knowledge Base Layout

Recommended layout:

```text
S3
├── baseline/
│   └── nist/
│       └── 800-53/
│           └── <version>/
│               ├── AC-1.md
│               ├── AC-2.md
│               └── ...
│
└── engagement/
    ├── customer-standards/
    ├── private-context/
    ├── uploads/
    └── generated-responses/
```

### Vector indexes

For one-engagement-per-environment:

- one baseline knowledge base/index;
- one engagement knowledge base/index.

The engagement index can contain both customer standards and private system context, differentiated with metadata.

### NIST baseline publishing

Treat NIST content as versioned reference data, not something downloaded opportunistically at request time.

Suggested publishing flow:

```text
NIST OSCAL JSON
      ↓
normalizer
      ↓
validate version/hash
      ↓
one normalized document per control
      ↓
S3 baseline prefix
      ↓
DynamoDB ControlCatalog
      ↓
Knowledge Base sync
```

This can be implemented later as a container task or a local administrative tool. It does not need to be part of the first interactive request path.

---

## 7. Request Lifecycle

For `Generate SI-5`:

### 7.1 Accept request

GUI submits:

```json
{
  "control_id": "SI-5",
  "analyst_notes": "...",
  "review": true
}
```

Return a job ID immediately. Generation should be asynchronous from the browser's perspective.

### 7.2 Validate control ID

DynamoDB `ControlCatalog` lookup.

If no exact control exists:

```text
STOP
"No matching canonical control."
```

No model call.

### 7.3 Retrieve canonical control material

Retrieve the exact control and a limited amount of related baseline context.

### 7.4 Retrieve exact engagement-specific standard

Filter by:

```text
tier = customer_standard
control_ids contains SI-5
```

Application code records whether an authoritative engagement-specific mapping was found.

### 7.5 Retrieve private system context

Use semantic retrieval against `private_context`, constrained to a small top-K.

### 7.6 Assemble structured prompt

Do not dump all retrieved text indiscriminately into context. Include source identifiers and source tiers.

### 7.7 Generate with Nemotron

Expected structured response:

```json
{
  "needs_info": false,
  "question": null,
  "draft": "...",
  "suggested_evidence": [
    "..."
  ],
  "missing_facts": []
}
```

If the model says additional information is required, return the question to the GUI rather than fabricating facts.

### 7.8 Review with Gemma 4 E2B

Reviewer receives:

- requested control;
- grounding packet;
- generated draft;
- explicit review rubric.

Reviewer should identify:

- unsupported implementation claims;
- missed control requirements;
- conflicts with authoritative customer standards;
- vague statements that should name an owner, cadence, mechanism, or evidence source;
- useful evidence suggestions.

### 7.9 Optional revision

If the reviewer identifies material defects, Nemotron receives the original grounding packet plus review findings and produces a revised draft.

Keep this to one revision pass in the MVP.

### 7.10 Persist

Store:

- final response;
- source document IDs;
- control ID;
- model IDs;
- model usage/tokens;
- timestamps;
- review findings;
- missing-fact markers;
- prompt/template version.

Do **not** log entire private prompts into CloudWatch by default.

---

## 8. Missing Information Policy

One of the most important SRG behaviors to preserve is the refusal to silently invent implementation facts.

Recommended policy:

1. Model may ask one focused follow-up question.
2. User supplies additional facts.
3. If facts remain unavailable, generate a best-effort draft with explicit placeholders.

Example:

```text
[PLACEHOLDER: Identify the team or role responsible for receiving
and evaluating vendor security advisories.]
```

A polished false claim is worse than an obvious placeholder.

---

## 9. Model Configuration

### MVP defaults

```text
generator = NVIDIA Nemotron 3 Nano 30B A3B
reviewer  = Google Gemma 4 E2B
```

Current US East Bedrock on-demand pricing checked August 2026:

```text
Nemotron 3 Nano 30B A3B
  input:  $0.06 / 1M tokens
  output: $0.24 / 1M tokens

Gemma 4 E2B
  input:  $0.04 / 1M tokens
  output: $0.08 / 1M tokens
```

Keep model identifiers in configuration rather than hard-coding them.

Suggested interface:

```text
generate(system_prompt, messages, response_schema, options)
review(control, grounding, draft, rubric)
```

This keeps a future SageMaker backend possible without restructuring the rest of the application.

---

## 10. Fargate Worker Model

### Do not use one `RunTask` per user request

A task started per generation would pay Fargate startup latency on every request. Completed tasks are not kept warm.

For an existing SRG experience of roughly 30 seconds per warmed request, this is undesirable.

### Use an ECS service with a warm worker

```text
ECS service
desired_count = 1
      │
      ▼
worker long-polls SQS
      │
      ├── request arrives
      ├── process it
      └── return to long poll
```

The worker is not hosting an LLM. It only orchestrates managed AWS services, so a small task should be sufficient initially.

Suggested starting size:

```text
0.25 vCPU
1 GB memory
```

Tune after measuring.

### Why this is operationally attractive

One container means:

- one Dockerfile;
- one dependency lockfile;
- one runtime version;
- one ECR repository;
- one vulnerability/SBOM lifecycle;
- one task role;
- one network policy;
- one patching/rebuild process.

ECR + Amazon Inspector can be enabled for container vulnerability inspection.

---

## 11. Development Profile vs Customer Profile

Implement this as a **small Terraform decision**, not a complex autoscaling system.

### Recommended variable

```hcl
variable "deployment_profile" {
  type    = string
  default = "dev"

  validation {
    condition     = contains(["dev", "customer"], var.deployment_profile)
    error_message = "deployment_profile must be dev or customer."
  }
}

locals {
  worker_desired_count = var.deployment_profile == "customer" ? 1 : 0
}
```

### Dev behavior

Default:

```text
desired_count = 0
```

When beginning a development session:

```bash
aws ecs update-service \
  --cluster <cluster> \
  --service <service> \
  --desired-count 1
```

When finished:

```bash
aws ecs update-service \
  --cluster <cluster> \
  --service <service> \
  --desired-count 0
```

Wrap these in:

```text
make worker-up
make worker-down
```

or simple scripts.

This means:

- no worker cost if the project sits untouched for three months;
- only one cold start at the beginning of a work session;
- every subsequent request in that session is warm;
- a Terraform deploy does not unexpectedly start the worker if desired count is zero.

### Customer behavior

```text
desired_count = 1
```

Keep the worker warm continuously. The likely extra cost is small relative to user labor and predictable response latency is more valuable.

### Future enhancement

Scheduled scaling or queue-based scale-to-zero can be added later for customer sandbox/nonproduction deployments. Do not build it into the MVP unless there is a real requirement.

---

## 12. Networking Profiles

### Personal development account

Optimize for simplicity and low fixed cost.

Recommended:

- Fargate task in public subnet;
- assign public IPv4;
- strict security group with no inbound rules;
- outbound TLS to required AWS endpoints;
- no NAT Gateway;
- no PrivateLink interface endpoints initially;
- CloudFront/API Gateway are public;
- Cognito authenticates the user.

The worker does not listen for inbound connections, so it does not need an ALB.

### Customer/compliance profile

Terraform can later enable:

- private subnets;
- no public task IP;
- VPC endpoints/PrivateLink for applicable AWS services;
- stricter egress controls;
- customer-managed KMS keys;
- organization-specific logging and retention;
- additional CloudTrail/Config/Security Hub controls.

Do not pay customer-grade networking fixed costs in the personal MVP unless needed for a demonstration.

---

## 13. GUI

Use a static SPA:

```text
React / Vue / Svelte
        ↓
npm build
        ↓
S3
        ↓
CloudFront
```

MVP screens:

1. **Login**
2. **Engagement status**
   - baseline version
   - last knowledge-base sync
   - document counts
3. **Document management**
   - upload engagement standard
   - upload private context
   - start/review ingestion status
4. **Generate response**
   - control ID
   - analyst notes
   - generate
5. **Clarification**
   - model follow-up question
   - user answer
6. **Response review**
   - draft
   - reviewer findings
   - missing facts
   - suggested evidence
   - copy/download Markdown
7. **History**
   - previous generated controls

Do not build a rich editor first. A textarea plus rendered Markdown is sufficient for MVP validation.

---

## 14. Terraform Layout

Recommended repository:

```text
srg-cloud/
├── app/
│   ├── frontend/
│   └── worker/
│
├── infra/
│   ├── modules/
│   │   ├── auth/
│   │   ├── frontend/
│   │   ├── api/
│   │   ├── storage/
│   │   ├── vectors/
│   │   ├── knowledge-base/
│   │   ├── inference/
│   │   ├── workflow/
│   │   ├── worker/
│   │   ├── observability/
│   │   └── budget/
│   │
│   └── environments/
│       └── dev/
│
├── scripts/
│   ├── worker-up.sh
│   ├── worker-down.sh
│   ├── deploy-frontend.sh
│   └── sync-kb.sh
│
├── docs/
│   └── SRG_AWS_Cloud_Rebuild_Implementation_Guide.md
│
├── Makefile
└── README.md
```

### High-value root variables

```hcl
aws_region              = "us-east-1"
deployment_profile      = "dev"
generator_model_id      = "..."
reviewer_model_id       = "..."
enable_customer_kms     = true
enable_private_network  = false
enable_container_scan   = true
enable_waf              = false
monthly_budget_usd      = 50
```

Avoid creating one IAM role per conceptual workflow step. Prefer a small number of narrowly scoped roles matching real execution principals:

- Step Functions execution role;
- ECS task execution role;
- ECS worker task role;
- Bedrock Knowledge Base service role.

---

## 15. Security Baseline

Even for the personal MVP:

- S3 Block Public Access enabled;
- Cognito required for application access;
- encrypted S3/DynamoDB/vector data;
- ECR immutable tags or image digest pinning for deployments;
- container scanning enabled;
- no inbound worker security-group rule;
- least-privilege IAM;
- CloudTrail enabled at the account level if not already present;
- no credentials baked into image or frontend;
- secrets in Secrets Manager/SSM only if secrets become necessary;
- retain source IDs and model/template versions for traceability;
- avoid logging raw private prompts and retrieved context;
- use Terraform tags consistently.

Recommended tags:

```text
Project     = "srg-cloud"
Environment = "dev"
ManagedBy   = "terraform"
```

---

## 16. Cost Envelope

These are planning estimates, not quotes. Recheck AWS pricing before deployment.

### AI inference

Representative request assumption:

```text
Nemotron generator:
  ~20K input
  ~4K output

Gemma reviewer:
  ~12K input
  ~2K output
```

At current US pricing, this is only a few thousandths of a dollar per generation/review cycle.

Expected model spend:

```text
1,000 requests/month: roughly a few dollars
  100 requests/month: well under one dollar
```

Repeated development experiments, retries, and optional revision passes should still keep inference comfortably below tens of dollars.

### Warm Fargate worker

A continuously running Linux/x86 Fargate task at approximately:

```text
0.25 vCPU
1 GB RAM
```

is roughly **$10-11/month** in us-east-1 at current rates, before public IPv4 or other networking.

A public IPv4 adds roughly a few dollars per month if left allocated continuously.

With `deployment_profile = "dev"` and desired count zero when not working, much of this fixed cost disappears.

### Expected overall monthly spend

#### Active MVP development (~1,000 requests)

Likely:

```text
~$15-40/month
```

Conservative planning envelope:

```text
$50/month
```

#### Refinement (~100 requests)

Likely:

```text
~$5-20/month
```

depending mainly on whether the worker remains running and on logging/networking choices.

### Treat these as anomalies

```text
$50/month  → review but not alarming during active development
$100/month → investigate
$150/month → likely a fixed-cost resource or logging mistake
$1,000     → architecture/provisioning error, not normal SRG usage
```

### Avoid these in the personal MVP

- NAT Gateway;
- OpenSearch Serverless;
- always-on SageMaker;
- unnecessary interface VPC endpoints;
- ALB;
- RDS;
- verbose prompt/context logging;
- elaborate WAF rule sets.

---

## 17. Cost Guardrails

Create AWS Budgets alerts from Terraform.

Suggested thresholds:

```text
$25   informational
$50   investigate
$100  urgent investigation
$150  stop and identify the resource
```

Also:

- enable Cost Explorer;
- tag every resource;
- consider anomaly detection if available/useful;
- make `terraform destroy` practical for disposable resources;
- keep data-bearing S3 buckets separable from disposable compute resources.

---

## 18. Observability

Track enough to troubleshoot quality and latency without leaking engagement content.

Structured event fields:

```text
job_id
control_id
request_stage
duration_ms
generator_model
reviewer_model
input_tokens
output_tokens
retrieved_source_ids
knowledge_base_sync_version
prompt_template_version
result_status
```

Do not log by default:

```text
full private source documents
complete RAG context
complete prompts
complete generated response text
user credentials/tokens
```

Useful CloudWatch metrics:

- total jobs;
- successful jobs;
- failed jobs;
- clarification-required jobs;
- median/p95 end-to-end latency;
- generator latency;
- reviewer latency;
- retrieved chunk count;
- token consumption;
- queue depth;
- worker health.

---

## 19. Quality Evaluation Is More Important Than Model Benchmark Scores

Build a regression corpus before relying on the tool.

Target 30-50 representative controls.

For each case store:

```text
control
authoritative customer requirement
known system facts
known missing facts
reference response
claims the model must not make
expected evidence suggestions
```

Score candidate outputs for:

1. control requirement coverage;
2. customer-standard precedence;
3. unsupported claims;
4. correct missing-information behavior;
5. appropriate follow-up question;
6. assessor-quality prose;
7. evidence suggestions;
8. reviewer usefulness.

The production default model should be selected using this task-specific corpus, not MMLU or general leaderboards.

---

## 20. Suggested MVP Phases

### Phase 0 — Repository and guardrails

- create repository;
- commit this guide;
- configure Terraform backend strategy;
- configure AWS provider;
- create tags/budgets;
- create ECR;
- create initial Docker worker skeleton;
- create static frontend skeleton.

**Exit condition:** `terraform apply` creates a cheap empty environment and budget alerts.

### Phase 1 — Canonical control catalog

- normalize a pinned NIST release;
- store per-control documents;
- populate `ControlCatalog`;
- exact control validation API/worker logic.

**Exit condition:** `AC-2` validates and an invalid control deterministically fails.

### Phase 2 — Engagement ingestion

- S3 upload workflow;
- engagement/customer standard prefix;
- private-context prefix;
- S3 Vectors;
- Bedrock Knowledge Base;
- metadata conventions;
- sync/status.

**Exit condition:** a test query returns only expected engagement material with source IDs.

### Phase 3 — Single-pass generation

- retrieval;
- prompt assembly;
- Nemotron generation;
- structured response validation;
- persist result;
- display in GUI.

**Exit condition:** one control can be generated end-to-end from the browser.

### Phase 4 — Missing facts

- clarification state;
- resume job after user response;
- placeholder policy.

**Exit condition:** the system visibly refuses to invent a known missing fact.

### Phase 5 — Reviewer

- Gemma review rubric;
- one revision pass;
- reviewer findings in GUI.

**Exit condition:** seeded bad drafts are reliably flagged.

### Phase 6 — Operational hardening

- container scanning;
- health metrics;
- CloudWatch alarms;
- retry/error policy;
- cost dashboard/budgets;
- backup/versioning policy.

### Phase 7 — Customer deployment profile

- `deployment_profile = "customer"`;
- warm worker;
- customer-controlled networking/KMS choices;
- tighter audit configuration;
- deployment documentation.

---

## 21. Laptop-Driven Deployment Workflow

Keep the first implementation intentionally simple.

Typical flow:

```bash
# infrastructure
cd infra/environments/dev
terraform init
terraform plan
terraform apply

# worker
docker build -t srg-worker .
docker tag ...
docker push ...
terraform apply

# start work session in dev
make worker-up

# frontend
npm ci
npm run build
aws s3 sync dist/ s3://<frontend-bucket>/
aws cloudfront create-invalidation ...

# end work session
make worker-down
```

Do not build CI/CD until repeated manual deployment becomes a real source of error or wasted time.

---

## 22. Resume-Here Checklist

When returning to this project after a week or a month:

1. Read **Sections 2, 3, 7, 10, and 20** of this guide.
2. Check the AWS account's current monthly spend.
3. Run `terraform plan` before changing anything.
4. Confirm the dev ECS worker is still at desired count 0 before beginning.
5. Run `make worker-up`.
6. Check the latest completed MVP phase.
7. Implement only the next exit condition.
8. Add or update regression cases for every interesting failure.
9. Run the task-specific model eval set before changing prompts/models.
10. Run `make worker-down` when finished.

---

## 23. Codex Handoff

The most reliable way to move this design into OpenAI Codex is to commit the Markdown version of this guide into the repository, for example:

```text
docs/SRG_AWS_Cloud_Rebuild_Implementation_Guide.md
```

Then start Codex in that repository and tell it to read the guide before making changes.

Suggested initial Codex instruction:

```text
Read docs/SRG_AWS_Cloud_Rebuild_Implementation_Guide.md completely before
making changes.

We are implementing the AWS-native SRG MVP described there. Preserve the
documented design decisions unless a current AWS limitation makes one
impossible. Do not introduce Lambda, EC2, OpenSearch Serverless, RDS, a NAT
Gateway, an ALB, or CI/CD unless the guide's current phase explicitly requires
it or you explain why it is unavoidable.

Work one MVP phase at a time. Before coding, summarize the phase's exit
condition and the files/resources you intend to create. Keep Terraform
modular but avoid abstraction for its own sake. Prefer direct Step Functions
AWS integrations for simple AWS-to-AWS calls and put real application logic
in the single Fargate worker container.

Keep the personal dev profile cheap:
- ECS desired count defaults to 0;
- no per-request Fargate tasks;
- no expensive fixed-cost services;
- preserve a future customer deployment profile.

After each phase:
1. run formatting/validation/tests;
2. report resources that create fixed monthly cost;
3. update the implementation status in the repository documentation.
```

As of August 2026, ChatGPT and Codex histories remain separate product views. Do not assume a new Codex thread automatically has this ChatGPT conversation. The repository guide is therefore the durable handoff mechanism.

---

## 24. Deferred Decisions

Do not solve these until evidence demands it:

- exact SPA framework;
- SQS vs another lightweight job-state mechanism;
- exact Bedrock prompt API adapter;
- detailed model failover;
- multi-user RBAC;
- multi-engagement-per-environment support;
- automated NIST update pipeline;
- CI/CD;
- WAF;
- fully private VPC networking;
- SageMaker inference backend;
- bulk generation UX;
- document export beyond Markdown;
- organization-wide deployment patterns.

The goal is a useful MVP, not a reference architecture contest.

---

## 25. Reference Links

Checked during architecture planning in August 2026.

### Existing SRG

- Repository: https://github.com/buggycrash/security-response-generator
- README: https://github.com/buggycrash/security-response-generator/blob/main/README.md
- Technical README: https://github.com/buggycrash/security-response-generator/blob/main/docs/technical-readme.md

### AWS

- Amazon Bedrock pricing: https://aws.amazon.com/bedrock/pricing/
- AWS Fargate pricing: https://aws.amazon.com/fargate/pricing/
- Amazon S3 / S3 Vectors pricing: https://aws.amazon.com/s3/pricing/
- S3 Vectors metadata filtering: https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-metadata-filtering.html
- S3 Vectors: https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors.html
- Bedrock Knowledge Base vector-store setup: https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html
- Bedrock S3 data source: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-ds-s3.html
- Step Functions + ECS/Fargate: https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html
- ECS Service Auto Scaling: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-auto-scaling.html

### OpenAI / Codex handoff

- ChatGPT Work and Codex: https://help.openai.com/en/articles/20001275/
- Codex app overview: https://openai.com/index/introducing-the-codex-app/

---

## 26. Final MVP North Star

A successful MVP lets one engineer:

```text
upload authoritative engagement material
           ↓
upload private system context
           ↓
enter a valid NIST control + notes
           ↓
receive a grounded draft
           ↓
be asked for missing facts instead of receiving invented ones
           ↓
receive an independent model review
           ↓
revise and export assessor-ready language
```

with infrastructure that is:

```text
Terraform-managed
AWS-native
cheap while idle
predictable while in use
auditable
replaceable at the model boundary
maintainable by one engineer
```

That is the capability to preserve. Everything else is implementation detail.
