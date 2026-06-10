# 01 Master Strategy and Architecture

## Purpose
This document is the primary handoff for the company vision, product structure, architecture direction, and execution model.

## Company vision
Build one shared-core platform with two monetization layers:
- Africa-first crypto and stablecoin orchestration API
- AI-assisted market-movement and arbitrage intelligence platform

## Strategic thesis
The moat comes from shared connectors, routing, compliance, proprietary data, and multi-agent intelligence rather than from a single narrow trading feature.

## Product pillars
- shared-core infrastructure
- partner-grade API platform
- operator-grade market intelligence
- compliance and trust by design
- multi-agent reasoning and debate

## Role-based product surfaces
- Trader/User intelligence console
- Developer/Partner orchestration console
- Admin/Ops control console

## Architecture summary
- GCP-based event-driven architecture
- Cloud Run services
- Pub/Sub event bus
- Cloud SQL for transactional data
- Redis for hot state
- BigQuery for analytics
- Cloud Storage for archives
- Secret Manager for credentials
- multi-agent intelligence layer above shared-core services

## Build order
1. shared core
2. auth/onboarding/compliance/billing
3. dashboards and control planes
4. market data and signal systems
5. Africa API plane
6. agent orchestration layer
7. live production hardening and onboarding
