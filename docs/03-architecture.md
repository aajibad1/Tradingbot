# 03 Architecture

## Purpose
Defines the technical architecture for the dual-business multi-agent platform.

## Main layers
- user-facing applications
- auth and tenant layer
- multi-agent intelligence layer
- shared-core domain layer
- trading plane
- Africa API plane
- data and analytics platform

## Shared-core components
- connector-runtime
- normalization-service
- route-optimizer
- venue-health-service
- compliance-screening-service
- event-router

## Multi-agent layer
- agent orchestrator
- Claude logic agent
- market agent
- finance agent
- debate agent
- execution guard agent

## Data layers
- Cloud SQL for transactional state
- Redis for hot state
- BigQuery for analytics
- Cloud Storage for archives
