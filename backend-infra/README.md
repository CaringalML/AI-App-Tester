# backend-infra

Terraform for the scan API: one Fargate task behind an Application Load Balancer,
served at `api.nodepulsecaringal.xyz` through Cloudflare, with DynamoDB for scan
records, S3 for evidence screenshots, and Secrets Manager for the Claude key.

```
Browser ──TLS (Cloudflare cert)──> Cloudflare ──TLS (ACM cert, strict)──> ALB ──> Fargate task
                                                                                 ├─> DynamoDB (scans)
                                                                                 ├─> S3 (screenshots)
                                                                                 └─> Claude API, sites under test
```

## Why it looks like this

- **ECS service with one task, not a task per scan.** A fresh Fargate task pulling a Chromium image takes 30 to 90 seconds to start. An always-running task means a scan starts the moment it is requested, which matters in a live demo.
- **No NAT gateway.** The task sits in a public subnet with a public IP for outbound traffic. Its security group accepts connections only from the load balancer, so it is not reachable directly. This saves roughly 35 USD a month over a private subnet and NAT.
- **The load balancer only accepts Cloudflare.** Its security group allows port 443 from Cloudflare's published ranges only, pulled live by Terraform. Nobody can bypass Cloudflare, and the `cf-connecting-ip` header the API rate-limits on cannot be forged.
- **Same TLS pattern as the frontend.** Cloudflare presents its own certificate to browsers. The ALB holds an ACM certificate for `api.nodepulsecaringal.xyz`, so the zone's `strict` SSL mode (owned by `frontend-infra`) verifies the origin too.
- **The Claude key never touches Terraform.** Terraform creates an empty secret; you put the value in with the AWS CLI. ECS injects it into the container at start. It is not in state, the image, the task definition, or git.
- **CI owns the image, Terraform owns the shape.** The service ignores task definition changes. Each deploy copies the newest registered revision and swaps only the image, pinned to the commit SHA. Change an environment variable here, `apply`, and the next deploy picks it up.

| File | Purpose |
| --- | --- |
| `network.tf` | VPC, two public subnets, security groups |
| `edge.tf` | ACM certificate, ALB and HTTPS listener, the proxied `api` DNS record |
| `ecs.tf` | Cluster, task definition, service with automatic rollback |
| `storage.tf` | ECR, DynamoDB with TTL, screenshot bucket with 7-day expiry, the secret, logs |
| `iam.tf` | Execution role, task role, GitHub deploy role (reuses the OIDC provider from `frontend-infra`) |

## First-time setup

Run this after `frontend-infra` has been applied, since it reuses that stack's GitHub OIDC provider and zone SSL setting.

```bash
cd backend-infra
cp terraform.tfvars.example terraform.tfvars
export TF_VAR_cloudflare_api_token="..."    # same token as frontend-infra
terraform init
terraform apply
```

Then store the Claude key in the secret Terraform created:

```bash
aws secretsmanager put-secret-value \
  --secret-id ai-app-tester/anthropic-api-key \
  --secret-string "sk-ant-..." --region ap-southeast-2
```

Set two repository variables (Settings, Secrets and variables, Actions, Variables):

| Variable | Value |
| --- | --- |
| `BACKEND_DEPLOY_ROLE_ARN` | `terraform output -raw github_deploy_role_arn` |
| `API_BASE_URL` | `terraform output -raw api_url` |

Run the **Deploy backend** workflow. It tests, builds, pushes, rolls out, and
smoke-tests `https://api.nodepulsecaringal.xyz/healthz`. Then re-run **Deploy
frontend** so the site is rebuilt against the live API.

The service will report failed tasks between `apply` and the first deploy, since
the image does not exist yet. That is expected and clears once the workflow runs.

## Cost at rest

Approximately 70 USD a month if left running in Sydney, at list prices: about 43 for
the Fargate task (1 vCPU, 2 GB), about 20 for the load balancer, about 11 for the
public IPv4 addresses AWS now bills for, and pennies for DynamoDB, S3, ECR and logs.
Check the AWS pricing pages before relying on these. Scale the service to zero
between demos, which removes the task cost:

```bash
aws ecs update-service --cluster ai-app-tester --service ai-app-tester-api --desired-count 0
```

Claude usage is separate and reported per scan by the API.
