# 08 Infra GCP Terraform

## Purpose
Defines the target GCP infrastructure and Terraform module layout.

## Environment model
- dev
- staging
- prod

## GCP foundations
- one project per environment where practical
- separate service accounts per service domain
- Secret Manager for credentials
- VPC and private networking where required
- Cloud Armor for public edge

## Core services
- Cloud Run for stateless services
- Pub/Sub for event bus
- Cloud SQL Postgres for transactional state
- Memorystore Redis for hot state
- BigQuery for analytics
- Cloud Storage for archives
- Artifact Registry for images
- Cloud Build or GitHub Actions for CI/CD
- Cloud Logging, Monitoring, and Trace

## Terraform module layout
```text
infra/
  modules/
    cloud_run_service/
    pubsub_topic/
    cloudsql/
    memorystore/
    bigquery_dataset/
    secret/
    service_account/
    iam_binding/
    load_balancer/
  envs/
    dev/
    staging/
    prod/
```

## Deployment requirements
- preview and plan before apply
- environment-specific vars
- secret references, never inline secrets
- separate state per environment
- rollback strategy for infra changes

## Expansion task
Claude should turn this into Terraform modules and env stacks ready to deploy.
