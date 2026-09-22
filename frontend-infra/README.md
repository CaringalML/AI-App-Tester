# frontend-infra

Terraform for the AI App Tester static site: a private S3 bucket behind CloudFront,
served on `nodepulsecaringal.xyz` with Cloudflare handling the browser-facing TLS.

## Why Cloudflare terminates TLS, not CloudFront

The Cloudflare DNS record for the domain stays proxied (orange-clouded), so Cloudflare
issues its own certificate and is the only thing any visitor's browser ever negotiates
TLS with. That part didn't change. How CloudFront gets satisfied on the other end of
that connection did.

**First attempt, and why it didn't survive contact with a Free plan.** The original
version of this stack tried to avoid an ACM certificate entirely: leave CloudFront on
its default `*.cloudfront.net` certificate, and use a Cloudflare Origin Rule to rewrite
the Host header (and SNI) it sends to CloudFront, so CloudFront would see a request for
its own domain rather than for `nodepulsecaringal.xyz`. That rewrite lives behind
Cloudflare's Origin Rules "HostHeader override" capability, which turned out to be
gated to paid plans. Applying it against this Free-tier zone failed with
`not entitled to use the HostHeader override` — a straight entitlement error, not a
token permission problem, and not fixable by changing what the token can do.

**What's here instead**, in `acm.tf`. CloudFront gets registered with
`nodepulsecaringal.xyz` (and `www`) as an actual alias, backed by a real ACM
certificate requested in `us-east-1` — CloudFront requires that region for any
certificate regardless of where the distribution or bucket live. With the domain
configured as a proper alias, Cloudflare's ordinary reverse-proxy behaviour — forwarding
the request's real Host header and SNI unmodified — is already enough. No rewrite rule,
no paid-plan feature.

This does not change what a browser sees. The ACM certificate is presented only on the
private hop between Cloudflare and CloudFront, which no visitor ever touches directly.
It does mean the Cloudflare zone's SSL mode moved from `full` to `strict`, since
CloudFront now answers with a certificate that genuinely covers the domain, so
Cloudflare can verify it instead of merely encrypting blindly.

The one real cost is the one this design set out to avoid: an ACM certificate to
request, validate by DNS (`cloudflare_record.cert_validation` in `acm.tf` proves
ownership), and implicitly renew for as long as this stack exists. ACM renews
certificates it manages automatically as long as the validation records stay in place,
so this is closer to a one-time setup cost than an ongoing one.

## What each file does

| File | Purpose |
| --- | --- |
| `versions.tf` | Provider version pins. AWS on v5, Cloudflare pinned to v4 — v5 renamed most resources. |
| `variables.tf` | Every input, each with an explanation of what changing it does. |
| `providers.tf` | AWS provider (plus a us-east-1 alias for the ACM certificate) and the Cloudflare provider. |
| `locals.tf` | Bucket naming, the zone lookup, tags. |
| `s3.tf` | Private bucket, no public access, versioned, SSE, a lifecycle rule to expire old versions. |
| `acm.tf` | The certificate CloudFront needs to answer to this domain, and why it exists — see above. |
| `cloudfront.tf` | The distribution: OAC to read S3, SPA fallback routing, security headers, the ACM certificate. |
| `cloudflare.tf` | DNS records for the site, and the zone SSL mode. |
| `iam.tf` | GitHub OIDC provider and a deploy role scoped to one repo and branch. |
| `outputs.tf` | Values the GitHub Actions workflow needs as repository variables. |

## First-time setup

```bash
cd frontend-infra
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: only the defaults need changing if anything moved

export TF_VAR_cloudflare_api_token="..."   # Zone.DNS edit, Zone.Zone Settings edit,
                                            # Zone.Zone read — scoped to the
                                            # nodepulsecaringal.xyz zone only

terraform init
terraform plan
terraform apply
```

AWS credentials come from whatever the AWS CLI already resolves locally (a profile,
`AWS_PROFILE`, or `aws sso login`) — nothing AWS-specific needs exporting beyond that.

The first `apply` takes longer than a typical Terraform run, usually a few minutes.
It has to wait on `aws_acm_certificate_validation.site`, which polls until Cloudflare's
DNS validation record has propagated and ACM has actually verified it — this is normal,
not a hang.

After `apply`, wire the outputs into the repository so `frontend-deploy.yml` can use them:

```bash
terraform output -raw github_deploy_role_arn
terraform output -raw s3_bucket_name
terraform output -raw cloudfront_distribution_id
```

Set those as **repository variables** (not secrets — none of the three is sensitive)
under Settings → Secrets and variables → Actions → Variables:

- `AWS_DEPLOY_ROLE_ARN`
- `S3_BUCKET_NAME`
- `CLOUDFRONT_DISTRIBUTION_ID`

Push to `main` and `frontend-deploy.yml` builds, syncs to S3, and invalidates the
CloudFront cache.

## What's deliberately left out

- **No remote state backend.** One operator, one machine, for a two-week prototype.
  Local `terraform.tfstate` is gitignored. Add an S3 backend before a second person
  needs to run `apply`.
- **No `terraform apply` in CI.** The deploy workflow only builds and ships the app.
  Applying infrastructure changes from a push is a bigger blast radius than this
  prototype's timeline has room to get right — state locking, plan review, and a
  rollback story all need to exist first.
- **No WAF, no multi-region failover, no custom error pages beyond the SPA fallback.**
  None of that changes what the panel is evaluating.
