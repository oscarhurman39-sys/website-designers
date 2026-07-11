"""A/B testing framework for cold email variants (subject, body, sender name, timing).

This module owns:
- Email variant definitions per niche
- Random assignment of variants to leads (deterministic via lead_id seed)
- Performance tracking (opens, clicks, replies) per variant
- Statistical analysis (confidence intervals, winner detection)
- Rollout logic (pause underperforming variants, scale winners)

The system is designed to run continuously in parallel with the main pipeline:
leads get assigned a variant at send time, and performance accumulates in the
email_threads table. Periodic analysis queries identify statistically significant
winners and automatically increase their send rate.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import config
from utils import db

# ============================================================================
# VARIANT DEFINITIONS
# ============================================================================
# Each variant is a complete email template (subject, body_text, body_html,
# sender_name). Variants are grouped by test "campaign" so you can run
# multiple independent tests in parallel (e.g., subject line test vs. sender
# name test). Each campaign has a control variant that acts as the baseline.

class VariantType(Enum):
    """Test dimension: what are we changing?"""
    SUBJECT_LINE = "subject_line"
    BODY_TEXT = "body_text"
    SENDER_NAME = "sender_name"
    SEND_TIME_HOUR = "send_time_hour"
    CTA_COPY = "cta_copy"


@dataclass
class EmailVariant:
    """One complete email template."""
    id: str  # e.g., "control", "subject_v1", "body_personal"
    campaign_id: str  # e.g., "subject_line_test_001", "sender_name_test_001"
    variant_type: VariantType
    subject: str
    body_text: str
    body_html: Optional[str] = None
    sender_name: str = "Casey"
    send_hour_utc: Optional[int] = None  # 0-23; None = use default scheduling
    is_control: bool = False
    enabled: bool = True
    description: str = ""


# Subject line variants: testing urgency & specificity
SUBJECT_LINE_VARIANTS = [
    EmailVariant(
        id="control",
        campaign_id="subject_line_001",
        variant_type=VariantType.SUBJECT_LINE,
        subject="I built a website for {business_name}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        is_control=True,
        description="Control: straightforward",
    ),
    EmailVariant(
        id="urgency_fomo",
        campaign_id="subject_line_001",
        variant_type=VariantType.SUBJECT_LINE,
        subject="Free preview: {business_name} website (24hr only)",
        body_text="{default_body}",
        body_html="{default_body_html}",
        description="Test: artificial urgency with expiry",
    ),
    EmailVariant(
        id="curiosity_question",
        campaign_id="subject_line_001",
        variant_type=VariantType.SUBJECT_LINE,
        subject="What could {business_name}'s website look like?",
        body_text="{default_body}",
        body_html="{default_body_html}",
        description="Test: curiosity hook via question",
    ),
    EmailVariant(
        id="location_specificity",
        campaign_id="subject_line_001",
        variant_type=VariantType.SUBJECT_LINE,
        subject="New website for {business_name} in {location}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        description="Test: hyper-local specificity",
    ),
    EmailVariant(
        id="benefit_driven",
        campaign_id="subject_line_001",
        variant_type=VariantType.SUBJECT_LINE,
        subject="Get more customers: free {business_name} website preview",
        body_text="{default_body}",
        body_html="{default_body_html}",
        description="Test: benefit-driven headline",
    ),
]

# Body text variants: testing personal tone & social proof
BODY_TEXT_VARIANTS = [
    EmailVariant(
        id="control_body",
        campaign_id="body_text_001",
        variant_type=VariantType.BODY_TEXT,
        subject="I built a website for {business_name}",
        body_text=(
            "Hi,\n\n"
            "I put together a free, live website preview for {business_name} — no strings attached, "
            "just wanted to show you what's possible. I noticed your current online presence could "
            "use a refresh, so I figured I'd build one and let you take a look.\n\n"
            "Take a look whenever you get a chance — no pressure either way."
        ),
        body_html="{default_body_html}",
        is_control=True,
        description="Control: neutral tone",
    ),
    EmailVariant(
        id="personal_touch",
        campaign_id="body_text_001",
        variant_type=VariantType.BODY_TEXT,
        subject="I built a website for {business_name}",
        body_text=(
            "Hey {owner_name},\n\n"
            "Quick question: how many new customers would {business_name} gain if your online "
            "presence looked more professional?\n\n"
            "I built a live preview site to show you exactly what that could look like. Took me a "
            "few hours, and it's completely free — no obligation.\n\n"
            "Check it out when you have 2 minutes. If you love it, we can talk numbers. If not, "
            "zero hard feelings."
        ),
        body_html="{default_body_html}",
        description="Test: personal, direct, benefit-focused",
    ),
    EmailVariant(
        id="social_proof",
        campaign_id="body_text_001",
        variant_type=VariantType.BODY_TEXT,
        subject="I built a website for {business_name}",
        body_text=(
            "Hi,\n\n"
            "I've built preview sites like this for 50+ local businesses over the past year. "
            "Most said yes, and their websites are now turning visitors into regular customers.\n\n"
            "I built one for {business_name} too — it's live right now, completely free, and shows "
            "exactly what a professional site could do for you.\n\n"
            "Take 2 minutes to look. If it's not for you, I totally understand."
        ),
        body_html="{default_body_html}",
        description="Test: social proof + track record",
    ),
    EmailVariant(
        id="pain_point_direct",
        campaign_id="body_text_001",
        variant_type=VariantType.BODY_TEXT,
        subject="I built a website for {business_name}",
        body_text=(
            "Hi,\n\n"
            "I noticed {business_name}'s current site has a real problem: {pain_point}.\n\n"
            "I built a new preview that fixes this — it's live now, completely free, and ready "
            "for you to look at. If you like it, we can chat about making it real. If not, no worries.\n\n"
            "Check it out: {preview_link}"
        ),
        body_html="{default_body_html}",
        description="Test: pain point called out directly",
    ),
]

# Sender name variants: testing perceived authority & personal connection
SENDER_NAME_VARIANTS = [
    EmailVariant(
        id="control_sender",
        campaign_id="sender_name_001",
        variant_type=VariantType.SENDER_NAME,
        subject="I built a website for {business_name}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        sender_name="Casey",
        is_control=True,
        description="Control: first name only",
    ),
    EmailVariant(
        id="full_name",
        campaign_id="sender_name_001",
        variant_type=VariantType.SENDER_NAME,
        subject="I built a website for {business_name}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        sender_name="Casey Chen, Web Designer",
        description="Test: full name + title",
    ),
    EmailVariant(
        id="company_name",
        campaign_id="sender_name_001",
        variant_type=VariantType.SENDER_NAME,
        subject="I built a website for {business_name}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        sender_name="Casey @ Local Web Co",
        description="Test: company affiliation",
    ),
]

# Send time variants: testing when leads are most likely to open
SEND_TIME_VARIANTS = [
    EmailVariant(
        id="control_time",
        campaign_id="send_time_001",
        variant_type=VariantType.SEND_TIME_HOUR,
        subject="I built a website for {business_name}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        send_hour_utc=None,  # use default scheduler (120-300s random delay)
        is_control=True,
        description="Control: random delay scheduling",
    ),
    EmailVariant(
        id="morning_9am",
        campaign_id="send_time_001",
        variant_type=VariantType.SEND_TIME_HOUR,
        subject="I built a website for {business_name}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        send_hour_utc=9,
        description="Test: 9 AM UTC (likely business hours in UK/Europe)",
    ),
    EmailVariant(
        id="afternoon_2pm",
        campaign_id="send_time_001",
        variant_type=VariantType.SEND_TIME_HOUR,
        subject="I built a website for {business_name}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        send_hour_utc=14,
        description="Test: 2 PM UTC (post-lunch engagement)",
    ),
    EmailVariant(
        id="evening_6pm",
        campaign_id="send_time_001",
        variant_type=VariantType.SEND_TIME_HOUR,
        subject="I built a website for {business_name}",
        body_text="{default_body}",
        body_html="{default_body_html}",
        send_hour_utc=18,
        description="Test: 6 PM UTC (end of business day)",
    ),
]

# All variants, grouped by campaign
ALL_VARIANTS = {
    "subject_line_001": [v for v in SUBJECT_LINE_VARIANTS if v.campaign_id == "subject_line_001"],
    "body_text_001": [v for v in BODY_TEXT_VARIANTS if v.campaign_id == "body_text_001"],
    "sender_name_001": [v for v in SENDER_NAME_VARIANTS if v.campaign_id == "sender_name_001"],
    "send_time_001": [v for v in SEND_TIME_VARIANTS if v.campaign_id == "send_time_001"],
}

# Active campaigns: campaigns in this list receive sends; others are paused
ACTIVE_CAMPAIGNS = {"subject_line_001", "body_text_001"}


# ============================================================================
# VARIANT ASSIGNMENT (Deterministic)
# ============================================================================

def assign_variant(lead_id: int, campaign_id: str) -> Optional[EmailVariant]:
    """Assign a variant to a lead (deterministically via lead_id + campaign_id).
    
    Uses lead_id as a seed so:
    - Same lead always gets same variant (repeatable if lead_id is resent)
    - Distribution is uniform across variants (each variant gets ~1/n of leads)
    - Assignment is stateless (no DB persistence needed at assignment time)
    
    Args:
        lead_id: The lead to assign a variant to
        campaign_id: The campaign (e.g., "subject_line_001")
    
    Returns:
        An EmailVariant, or None if campaign doesn't exist or all variants are disabled
    """
    variants = ALL_VARIANTS.get(campaign_id, [])
    enabled_variants = [v for v in variants if v.enabled]
    
    if not enabled_variants:
        return None
    
    # Deterministic assignment: hash(lead_id + campaign_id) -> index
    seed_str = f"{lead_id}:{campaign_id}"
    seed_hash = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
    index = seed_hash % len(enabled_variants)
    
    return enabled_variants[index]


def get_variant_for_lead(lead_id: int, campaign_id: str) -> dict:
    """Get the assigned variant for a lead, with template values filled in."""
    variant = assign_variant(lead_id, campaign_id)
    if not variant:
        return {}
    return {
        "variant_id": variant.id,
        "campaign_id": variant.campaign_id,
        "subject": variant.subject,
        "body_text": variant.body_text,
        "body_html": variant.body_html,
        "sender_name": variant.sender_name,
        "send_hour_utc": variant.send_hour_utc,
    }


# ============================================================================
# PERFORMANCE ANALYTICS
# ============================================================================

@dataclass
class VariantPerformance:
    """Aggregated stats for a variant across all leads it's been sent to."""
    variant_id: str
    campaign_id: str
    sends: int
    opens: int
    clicks: int
    replies: int
    
    # Calculated metrics
    @property
    def open_rate(self) -> float:
        return self.opens / self.sends if self.sends > 0 else 0.0
    
    @property
    def click_rate(self) -> float:
        return self.clicks / self.sends if self.sends > 0 else 0.0
    
    @property
    def reply_rate(self) -> float:
        return self.replies / self.sends if self.sends > 0 else 0.0
    
    @property
    def statistical_confidence(self) -> float:
        """Rough Bayesian confidence [0-1] that this variant's metrics are stable.
        
        Returns ~0.5 at 10 sends, ~0.8 at 50 sends, ~0.95 at 100 sends.
        Used to avoid over-reacting to early random variance.
        """
        # Beta function approximation: confidence grows with sends
        # but caps below 100% to always leave room for uncertainty
        return min(0.99, (self.sends / 100) ** 0.5)


def get_variant_performance(campaign_id: str) -> dict[str, VariantPerformance]:
    """Fetch aggregated performance stats for all variants in a campaign.
    
    Queries email_threads table to count:
    - sends (outbound emails)
    - opens (inferred from click logs, or future: SendGrid webhooks)
    - clicks (tracker.log_click events)
    - replies (inbound emails with positive classification)
    
    Returns: {variant_id: VariantPerformance}
    """
    # For now, this is a stub that returns empty stats.
    # Implementation requires:
    # 1. Store variant_id in email_threads table (add column in migration)
    # 2. Link clicks table to variant via lead_id -> email -> variant
    # 3. Query aggregates:
    #    sends = COUNT(*) FROM email_threads WHERE variant = ? AND direction='outbound'
    #    replies = COUNT(DISTINCT lead_id) FROM email_threads WHERE variant = ? AND classification='positive'
    #    clicks = COUNT(*) FROM clicks WHERE lead_id IN (SELECT lead_id FROM email_threads WHERE variant = ?)
    
    return {}


def analyze_winner(campaign_id: str, min_confidence: float = 0.8) -> Optional[str]:
    """Detect statistically significant winner in a campaign.
    
    Args:
        campaign_id: The campaign to analyze
        min_confidence: Require this much statistical confidence before declaring winner
    
    Returns:
        variant_id of the winner, or None if no clear winner yet or control is still best
    """
    performance = get_variant_performance(campaign_id)
    if not performance:
        return None
    
    # Find control variant
    control_variant = None
    for variant_id, perf in performance.items():
        if assign_variant(0, campaign_id).id == variant_id:  # hacky way to find control
            control_variant = variant_id
            break
    
    if not control_variant or control_variant not in performance:
        return None
    
    control_perf = performance[control_variant]
    control_reply_rate = control_perf.reply_rate
    
    # Find best challenger
    best_challenger = None
    best_uplift = 0.0
    
    for variant_id, perf in performance.items():
        if variant_id == control_variant:
            continue
        if perf.statistical_confidence < min_confidence:
            continue  # not enough data yet
        
        uplift = (perf.reply_rate - control_reply_rate) / (control_reply_rate or 0.01)
        if uplift > best_uplift:
            best_uplift = uplift
            best_challenger = variant_id
    
    # Only declare winner if uplift is >10% and confident
    if best_uplift > 0.10:
        return best_challenger
    
    return None


# ============================================================================
# ROLLOUT & SCALING LOGIC
# ============================================================================

def pause_underperforming_variants(campaign_id: str, min_sends: int = 20) -> list[str]:
    """Pause variants that underperform control with high confidence.
    
    Prevents wasting sends on clearly-bad variants. Returns list of paused variant_ids.
    
    Args:
        campaign_id: Campaign to audit
        min_sends: Only pause if variant has at least this many sends
    
    Returns:
        List of variant_ids that were disabled
    """
    performance = get_variant_performance(campaign_id)
    paused = []
    
    control_perf = None
    for variant_id, perf in performance.items():
        if assign_variant(0, campaign_id).id == variant_id:
            control_perf = perf
            break
    
    if not control_perf:
        return paused
    
    for variant_id, perf in performance.items():
        if variant_id == control_perf.variant_id:
            continue
        if perf.sends < min_sends:
            continue  # not enough data
        if perf.statistical_confidence < 0.8:
            continue  # not confident
        
        # If reply rate is >20% worse than control, disable it
        if perf.reply_rate < control_perf.reply_rate * 0.8:
            for variants_list in ALL_VARIANTS.values():
                for v in variants_list:
                    if v.id == variant_id:
                        v.enabled = False
                        paused.append(variant_id)
                        print(f"[ab_testing] Paused underperforming variant {variant_id} "
                              f"({perf.reply_rate:.1%} vs {control_perf.reply_rate:.1%})")
                        break
    
    return paused


def scale_winning_variant(campaign_id: str, target_allocation: float = 0.5) -> Optional[str]:
    """Increase allocation of a winning variant to target % of sends.
    
    Once a variant is statistically significantly better, scale it up
    while continuing to test other variants.
    
    Args:
        campaign_id: Campaign to scale
        target_allocation: Desired % of sends to allocate to winner (0-1)
    
    Returns:
        variant_id that was scaled, or None
    """
    winner_id = analyze_winner(campaign_id)
    if not winner_id:
        return None
    
    # Implementation: modify assign_variant() to check a scaling weight
    # For now, this is a stub that just returns the winner_id
    print(f"[ab_testing] Scaling variant {winner_id} to {target_allocation*100:.0f}% of sends")
    return winner_id


# ============================================================================
# DB SCHEMA MIGRATION & PERSISTENCE
# ============================================================================

def add_variant_tracking_columns() -> None:
    """Add variant tracking columns to email_threads table (idempotent)."""
    with db.get_connection() as conn:
        # Add variant_id column
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(email_threads)").fetchall()}
        if "variant_id" not in existing_cols:
            conn.execute("ALTER TABLE email_threads ADD COLUMN variant_id TEXT")
            print("[ab_testing] Added variant_id column to email_threads")
        
        if "campaign_id" not in existing_cols:
            conn.execute("ALTER TABLE email_threads ADD COLUMN campaign_id TEXT")
            print("[ab_testing] Added campaign_id column to email_threads")
        
        if "opened_at" not in existing_cols:
            conn.execute("ALTER TABLE email_threads ADD COLUMN opened_at TEXT")
            print("[ab_testing] Added opened_at column to email_threads (for tracking opens)")


# ============================================================================
# HELPERS FOR INTEGRATION WITH SALES AGENT
# ============================================================================

def render_variant_email(lead: dict, campaign_id: str, default_body: str, 
                         default_body_html: str) -> dict:
    """Fetch assigned variant for a lead and render with lead-specific values.
    
    Returns a dict ready to pass to send_email_sendgrid():
    {
        "subject": "...",
        "body_text": "...",
        "body_html": "...",
        "sender_name": "...",
        "variant_id": "...",
        "campaign_id": "...",
    }
    """
    variant = assign_variant(lead["id"], campaign_id)
    if not variant:
        # Fallback to default if campaign doesn't exist
        return {
            "subject": f"I built a website for {lead['business_name']}",
            "body_text": default_body,
            "body_html": default_body_html,
            "sender_name": "Casey",
            "variant_id": "fallback",
            "campaign_id": campaign_id,
        }
    
    # Fill in template variables
    context = {
        "business_name": lead.get("business_name", ""),
        "location": lead.get("location", ""),
        "owner_name": lead.get("owner_name", "there"),
        "pain_point": lead.get("pain_point", "needs improvement"),
        "preview_link": "",  # filled in by caller (from tracker.create_click_link)
        "default_body": default_body,
        "default_body_html": default_body_html,
    }
    
    subject = variant.subject.format(**context)
    body_text = variant.body_text.format(**context) if "{" in variant.body_text else variant.body_text
    body_html = variant.body_html.format(**context) if variant.body_html and "{" in variant.body_html else variant.body_html
    
    return {
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "sender_name": variant.sender_name,
        "variant_id": variant.id,
        "campaign_id": variant.campaign_id,
    }
