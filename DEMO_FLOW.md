# Demo Flow (Interview / Showcase)

## Goal
Show end-to-end capability across Excel, SQL analytics, CRM integration, process KPIs, prioritization, and automation.

## 1) Quick Project Context (1 minute)
- Open home dashboard and bug dashboard
- Mention this is a Django-based reward/productivity + quality analytics platform

## 2) Excel + SQL Capability (2 minutes)
- From Home, run `Export Commits (Excel)`
- From Bug Dashboard:
  - Download bug import template
  - Upload sample `.xlsx` file
- Explain:
  - data cleansing + validation on import
  - SQL-backed queries powering dashboard metrics

## 3) CRM Integration (2 minutes)
- Open `/crm/`
- Show Dynamics configuration form
- Trigger `Sync Contacts`
- Explain token flow + contact persistence (`CRMConnection`, `CRMContact`)

## 4) Data Enrichment (1 minute)
- Click `Enrich Contacts`
- Explain:
  - base domain enrichment always runs
  - optional external provider enrichment via `CLEARBIT_API_KEY`

## 5) Lean KPI + Prioritization (2 minutes)
- Show `Lean KPI Board`
- Show `Priority Scorecard`
- Explain scoring:
  - severity impact x age urgency x blocker weight
- Show assignee workload for cross-functional planning

## 6) Automated Workflows and Alerts (2 minutes)
- Create an alert rule in CRM dashboard
- Click `Run Alerts Now`
- Show recent alert events table updates
- Explain Slack dispatch and scheduled command:
  - `python manage.py run_crm_alerts`

## 7) Collaboration and Global Context (1 minute)
- Use org and timezone filters
- Show localized event timestamps
- Explain how this supports global teams

## 8) Close with Outcome (30 sec)
- Strong coverage of:
  - Excel + SQL analysis
  - CRM integrations
  - Process improvement metrics
  - Data-driven decision communication
  - Prioritization and automation
