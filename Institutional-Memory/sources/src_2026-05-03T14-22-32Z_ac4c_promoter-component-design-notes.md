---
id: src_2026-05-03T14-22-32Z_ac4c
type: source
source_type: markdown
canonical_url: https://example.com/friday/promoter
archive_record: arc_2026-05-03T14-22-32Z_ac4c
captured_at: '2026-05-03T14:22:32.203621Z'
last_verified: '2026-05-03T14:22:32.203621Z'
promoted_at: '2026-05-03T14:23:00.503774Z'
promotion_reason: relevance
status: active
artifacts:
- path: Archive/Clean/arc_2026-05-03T14-22-32Z_ac4c/promoter_notes.md
  type: clean
relations:
- type: elaborates_on
  target: Personal Second Brain
- type: is_source_for
  target: Promoter
- type: is_source_for
  target: Janitor
- type: depends_on
  target: 'Friday Architecture Proposal: Triage Layer'
- type: elaborates_on
  target: Archive-to-Memory Promotion
- type: elaborates_on
  target: Missing Data Principle
tags:
- knowledge-management
- architecture
- personal-second-brain
engagement: passing
title: Promoter Component Design Notes
summary: The Promoter component watches engagement and relevance signals to promote
  archive records into memory. It is intentionally biased toward over-promotion, treating
  lack of engagement as missing data rather than a negative signal.
---

# Promoter Component Design Notes

## Summary

The Promoter is a Friday subsystem that monitors engagement signals — including Slack reactions, Drive view duration, calendar attendance, comments, @mentions, and manual tags — and promotes archive-tier records to memory-tier records when engagement or relevance crosses defined thresholds. A core design principle is that absence of engagement is treated as missing data, not a negative signal, acknowledging that users have channels they lurk in or miss entirely.

The Promoter is deliberately biased toward over-promotion to avoid losing potentially relevant records. Cleanup of unused memory records is delegated to a separate Janitor component that runs a weekly demotion pass. Relevance-based promotion is triggered when a triage relevance_score reaches 0.7 or above.

## Key points

- The Promoter watches engagement signals (Slack reactions, Drive view duration, calendar attendance, comments, @mentions, sharing, manual @promote tag) to decide when to promote archive records to memory.
- Lack of engagement is treated as missing data, never as a negative signal — the Promoter never penalizes a record for low engagement.
- The Promoter is biased toward over-promotion; cleanup of unused memory records is handled by a separate Janitor component via a weekly demotion pass.
- Engagement triggers include: comments, reactions, attendance at small meetings (≤8 people), opening for >60 seconds, sharing forward, @mentioning someone, or manual @promote tag.
- Relevance-based promotion triggers when the triage relevance_score is ≥ 0.7.
- The Promoter and Janitor form a complementary pair: aggressive promotion balanced by periodic demotion.

## Notes

The 'missing data, not negative signal' principle is a meaningful design choice — it avoids the cold-start and lurker problems common in recommendation systems. The explicit split between promotion (Promoter) and demotion (Janitor) as separate components with separate cadences is architecturally clean and worth preserving as a pattern. The ≤8 person meeting threshold for attendance as an engagement signal is an interesting heuristic — it implies small meetings are higher-signal than large ones, which is reasonable but worth revisiting empirically.

## History
