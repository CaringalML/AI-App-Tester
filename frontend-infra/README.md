# frontend-infra

Terraform for the AI App Tester static site: a private S3 bucket behind CloudFront,
served on `nodepulsecaringal.xyz` with Cloudflare handling the browser-facing TLS.

## Why Cloudflare terminates TLS, not CloudFront

The usual pattern is an ACM certificate on CloudFront plus a Cloudflare DNS record set
to DNS-only. This stack does the opposite: the Cloudflare record stays proxied
(orange-clouded), Cloudflare issues its own certificate for the domain, and CloudFront
is left on its default `*.cloudfront.net` certificate.

That means no ACM certificate to request, validate by DNS, and wait on before the first
`apply` finishes, and one less certificate to renew over the life of the prototype.
Cloudflare already owned the domain, so it does the one thing CloudFront's default
certificate can't: answer for `nodepulsecaringal.xyz` by name.

The cost of that shortcut is `cloudflare.tf`. CloudFront routes purely on the Host
header, and it doesn't know this domain, so a request arriving as
`nodepulsecaringal.xyz` gets a 403 unless something rewrites it first. The origin
rule in that file rewrites the Host header, and the TLS SNI, to CloudFront's own
domain name before the request reaches it. The Cloudflare SSL mode is `full`, meaning
Cloudflare encrypts that hop but does not check the certificate name — it can't,
since CloudFront's certificate has no idea this domain exists.

## What each file does

| File | Purpose |
| --- | --- |
| `versions.tf` | Provider version pins. AWS on v5, Cloudflare pinned to v4 — v5 renamed most resources. |
| `variables.tf` | Every input, each with an explanation of what changing it does. |
| `providers.tf` | AWS and Cloudflare provider blocks. |
| `locals.tf` | Bucket naming, the zone lookup, tags. |
| `s3.tf` | Private bucket, no public access, versioned, SSE, a lifecycle rule to expire old versions. |
| `cloudfront.tf` | The distribution: OAC to read S3, SPA fallback routing, security headers, default certificate. |
| `cloudflare.tf` | DNS records, the origin host/SNI rewrite rule, the zone SSL mode. |
| `iam.tf` | GitHub OIDC provider and a deploy role scoped to one repo and branch. |
| `outputs.tf` | Values the GitHub Actions workflow needs as repository variables. |

## First-time setup

```bash
cd frontend-infra
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: only the defaults need changing if anything moved

export TF_VAR_cloudflare_api_token="..."   # Zone.DNS edit, Zone.Zone Settings edit,
                                            # Zone.Origin Rules edit, Zone.Zone read —
                                            # scoped to the nodepulsecaringal.xyz zone only.
                                            # Origin Rules, not Config Rules — that's a
                                            # different ruleset product and won't
                                            # authorize cloudflare_ruleset.origin_host_rewrite

terraform init
terraform plan
terraform apply
```

AWS credentials come from whatever the AWS CLI already resolves locally (a profile,
`AWS_PROFILE`, or `aws sso login`) — nothing AWS-specific needs exporting beyond that.

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
