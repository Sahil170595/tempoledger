"""Core scheduling engine with stochastic jitter algorithm."""

import logging
from datetime import UTC, datetime, time, timedelta
from uuid import UUID

import numpy as np

from tempoledger.config import settings
from tempoledger.exceptions import SchedulingError
from tempoledger.models import Campaign, Message, ScheduledMessage

logger = logging.getLogger(__name__)


class JitterScheduler:
    """
    stochastic message scheduling engine.

    Samples preparation delays and timing perturbations.
    These heuristics are not validated against a population of people.
    """

    def __init__(self, config=None, seed: int | None = None):
        """Initialize the scheduler with configuration."""
        self.config = config or settings
        self.last_pause_applied = False
        self.seed = seed
        self.rng = np.random.RandomState(seed)

    def schedule_message(
        self,
        message: Message,
        queue_history: list[ScheduledMessage],
        current_time: datetime,
        session_id: UUID,
    ) -> ScheduledMessage:
        """
        Schedule a message with stochastic timing.

        Args:
            message: The message to schedule
            queue_history: Recent scheduling history for pattern detection
            current_time: Current timestamp
            session_id: Session/campaign identifier

        Returns:
            ScheduledMessage with send_time and reasoning

        Raises:
            SchedulingError: If scheduling fails
        """
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("current_time must be timezone-aware")
        try:
            # 1. Calculate base typing time
            wpm = self._sample_wpm()
            word_count = len(message.content.split())
            typing_time = (word_count / wpm) * 60

            # 2. Add cognitive pauses
            pause_duration = 0.0
            if self._should_pause():
                pause_duration = self._sample_pause_duration()
                typing_time += pause_duration
                self.last_pause_applied = True
            else:
                self.last_pause_applied = False

            # 3. Check for temporal clustering opportunity
            base_target_time = current_time + timedelta(seconds=typing_time)
            next_cluster = self._next_cluster_time(current_time)
            cluster_bias = None

            if next_cluster and (next_cluster - current_time).total_seconds() < typing_time + 300:
                # Within 5 min of cluster time, bias toward it
                target_time = self._bias_toward_cluster(base_target_time, next_cluster)
                cluster_bias = next_cluster
            else:
                target_time = base_target_time

            # 4. Avoid pattern detection
            target_time = self._apply_interval_jitter(target_time, queue_history)

            # 5. Validate business hours
            # Note: When called from schedule_campaign, this will be overridden by
            # _enforce_business_hours_within_bounds to respect campaign duration
            target_time = self._enforce_business_hours(target_time)

            # Calculate jitter applied
            jitter_applied = (target_time - base_target_time).total_seconds()

            return ScheduledMessage(
                message=message,
                send_time=target_time,
                reasoning={
                    "prepared_from": current_time.isoformat(),
                    "typing_duration": typing_time,
                    "wpm_sampled": wpm,
                    "word_count": word_count,
                    "had_pause": self.last_pause_applied,
                    "pause_duration": pause_duration,
                    "cluster_bias": cluster_bias.isoformat() if cluster_bias else None,
                    "jitter_applied": jitter_applied,
                },
                session_id=session_id,
            )

        except Exception as e:
            logger.error(f"Failed to schedule message: {e}", exc_info=True)
            raise SchedulingError(f"Scheduling failed: {e}") from e

    def schedule_campaign(
        self, campaign: Campaign, start_time: datetime | None = None
    ) -> list[ScheduledMessage]:
        """
        Schedule all messages in a campaign.

        Args:
            campaign: The campaign to schedule
            start_time: Optional start time (defaults to now)

        Returns:
            List of scheduled messages sorted by send_time
        """
        if not campaign.messages:
            raise SchedulingError("Campaign has no messages")

        start = start_time or datetime.now(UTC)
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start_time must be timezone-aware")
        schedule = []
        queue_history: list[ScheduledMessage] = []

        # Distribute messages with stochastic clustering
        distribution = self._distribute_messages(len(campaign.messages), campaign.duration_hours)

        current_time = start
        for i, message in enumerate(campaign.messages):
            scheduled = self.schedule_message(
                message=message,
                queue_history=queue_history,
                current_time=current_time,
                session_id=campaign.id,
            )

            # Apply distribution strategy
            if i < len(distribution):
                # Adjust to match distribution plan (relative to start time)
                distribution_time = start + timedelta(seconds=distribution[i])
                # Clamp to campaign duration
                end_time = start + timedelta(hours=campaign.duration_hours)
                if distribution_time > end_time:
                    distribution_time = end_time
                if distribution_time > scheduled.send_time:
                    scheduled.send_time = distribution_time
                    scheduled.reasoning["distribution_adjusted"] = True

            # Enforce business hours within campaign duration
            end_time = start + timedelta(hours=campaign.duration_hours)
            scheduled.send_time = self._enforce_business_hours_within_bounds(
                scheduled.send_time, start, end_time
            )

            schedule.append(scheduled)
            queue_history.append(scheduled)

            # Update current time for next message
            current_time = scheduled.send_time

        # Sort by send_time
        schedule.sort(key=lambda m: m.send_time)

        logger.info(
            f"Scheduled {len(schedule)} messages for campaign {campaign.id}",
            extra={"campaign_id": str(campaign.id), "message_count": len(schedule)},
        )

        return schedule

    def _sample_wpm(self) -> float:
        """Sample words per minute from normal distribution."""
        wpm = self.rng.normal(self.config.default_wpm_mean, self.config.default_wpm_std)
        return np.clip(wpm, self.config.default_wpm_min, self.config.default_wpm_max)

    def _should_pause(self) -> bool:
        """Determine if a cognitive pause should occur."""
        return self.rng.random() < self.config.pause_probability

    def _sample_pause_duration(self) -> float:
        """Sample pause duration from exponential distribution."""
        # Exponential distribution with mean ~12s
        pause = self.rng.exponential(1.0 / self.config.pause_lambda)
        # Clamp to reasonable range (5-45s)
        return np.clip(pause, 5.0, 45.0)

    def _next_cluster_time(self, current_time: datetime) -> datetime | None:
        """Find the next temporal cluster opportunity."""
        current_minute = current_time.minute
        current_hour = current_time.hour

        # Peak hours: 10-11am, 2-3pm
        peak_hours = [10, 11, 14, 15]

        # Check if we're in a peak hour
        if current_hour in peak_hours:
            # Cluster around quarter-hours
            quarter_hours = [0, 15, 30, 45]
            for quarter in quarter_hours:
                if quarter > current_minute:
                    cluster_time = current_time.replace(minute=quarter, second=0, microsecond=0)
                    return cluster_time

        # Check next hour
        next_hour = current_hour + 1
        if next_hour in peak_hours:
            cluster_time = current_time.replace(hour=next_hour, minute=0, second=0, microsecond=0)
            return cluster_time

        return None

    def _bias_toward_cluster(self, base_time: datetime, cluster_time: datetime) -> datetime:
        """Bias target time toward a cluster time using normal distribution."""
        time_diff = (cluster_time - base_time).total_seconds()

        # If already past cluster, use base time
        if time_diff < 0:
            return base_time

        # Bias toward cluster with normal distribution (std=3min)
        bias_seconds = self.rng.normal(0, 180)  # 3 minutes
        target = base_time + timedelta(seconds=bias_seconds)

        # Ensure we don't go past cluster time
        if target > cluster_time:
            target = cluster_time

        return target

    def _apply_interval_jitter(
        self, proposed_time: datetime, history: list[ScheduledMessage]
    ) -> datetime:
        """
        Detect and avoid patterns in recent scheduling history.

        Checks:
        - No perfectly even spacing (variance test)
        - No repeated intervals (frequency analysis)
        - Dense recent event windows (heuristic delay, not a hard bound)
        """
        if len(history) < 3:
            return proposed_time

        try:
            # Calculate recent intervals
            intervals = []
            for i in range(1, len(history)):
                interval = (history[i].send_time - history[i - 1].send_time).total_seconds()
                intervals.append(interval)

            # If variance is too low, add extra jitter
            if len(intervals) > 0:
                variance = np.var(intervals)
                if variance < self.config.min_interval_variance:
                    extra_jitter = self.rng.gamma(shape=2, scale=30)
                    proposed_time += timedelta(seconds=extra_jitter)
                    logger.debug(
                        f"Low variance detected ({variance:.2f}), adding jitter: {extra_jitter:.2f}s"
                    )

                # If creating a repeated interval, perturb
                if history:
                    proposed_interval = (proposed_time - history[-1].send_time).total_seconds()
                    for interval in intervals:
                        if abs(proposed_interval - interval) < 5:
                            perturbation = self.rng.normal(0, 20)
                            proposed_time += timedelta(seconds=perturbation)
                            logger.debug(
                                f"Repeated interval detected, perturbing: {perturbation:.2f}s"
                            )
                            break

                # Check for burst (too many messages in short time)
                if len(history) >= 2:
                    recent_times = [h.send_time for h in history[-self.config.max_burst_messages :]]
                    window_start = proposed_time - timedelta(
                        seconds=self.config.burst_window_seconds
                    )
                    messages_in_window = sum(1 for t in recent_times if t > window_start)
                    if messages_in_window >= self.config.max_burst_messages:
                        # Delay to avoid burst
                        delay = self.rng.uniform(30, 60)
                        proposed_time += timedelta(seconds=delay)
                        logger.debug(
                            f"Burst detected ({messages_in_window} messages), delaying: {delay:.2f}s"
                        )

        except Exception as e:
            logger.warning(f"Error in interval jitter: {e}", exc_info=True)
            # Continue with proposed time if analysis fails

        return proposed_time

    def _enforce_business_hours(self, target_time: datetime) -> datetime:
        """
        Ensure target time is within business hours (forward-only adjustment).

        This method adjusts times to business hours, always moving forward
        to maintain chronological ordering. For campaign-level enforcement
        with duration bounds, use _enforce_business_hours_within_bounds instead.

        Args:
            target_time: Time to enforce

        Returns:
            Adjusted time within business hours (always forward from input)
        """
        business_start = time(hour=self.config.business_hours_start, minute=0, second=0)
        business_end = time(hour=self.config.business_hours_end, minute=0, second=0)

        target_time_only = target_time.time()

        # If before business hours, move to start of same day
        if target_time_only < business_start:
            target_time = target_time.replace(
                hour=self.config.business_hours_start, minute=0, second=0, microsecond=0
            )
        # If after business hours, move to next day's business start (forward-only)
        elif target_time_only > business_end:
            # Move to next day's business start to maintain forward-only behavior
            target_time = target_time.replace(
                hour=self.config.business_hours_start, minute=0, second=0, microsecond=0
            )
            target_time += timedelta(days=1)

        return target_time

    def _enforce_business_hours_within_bounds(
        self, target_time: datetime, start_time: datetime, end_time: datetime
    ) -> datetime:
        """
        Ensure target time is within business hours AND campaign bounds.

        Args:
            target_time: Time to enforce
            start_time: Campaign start time
            end_time: Campaign end time

        Returns:
            Adjusted time within both business hours and campaign bounds
        """
        business_start = time(hour=self.config.business_hours_start, minute=0, second=0)
        business_end = time(hour=self.config.business_hours_end, minute=0, second=0)

        # First, clamp to campaign bounds
        if target_time < start_time:
            target_time = start_time
        elif target_time > end_time:
            target_time = end_time

        # Then enforce business hours, but only if within campaign bounds
        target_time_only = target_time.time()

        if target_time_only < business_start:
            # Try to move to business start, but only if within bounds
            business_start_time = target_time.replace(
                hour=self.config.business_hours_start, minute=0, second=0, microsecond=0
            )
            if business_start_time >= start_time and business_start_time <= end_time:
                target_time = business_start_time
            # Otherwise, use the start_time (campaign bound)
            elif target_time < start_time:
                target_time = start_time
        elif target_time_only > business_end:
            # Try to move to business end, but only if within bounds
            business_end_time = target_time.replace(
                hour=self.config.business_hours_end, minute=0, second=0, microsecond=0
            )
            if business_end_time >= start_time and business_end_time <= end_time:
                target_time = business_end_time
            # Otherwise, clamp to end_time (campaign bound)
            elif target_time > end_time:
                target_time = end_time

        return target_time

    def _distribute_messages(self, count: int, duration_hours: float) -> list[float]:
        """
        Distribute messages with stochastic clustering.

        Strategy:
        - 30% during peak hours (10-11am, 2-3pm)
        - 50% distributed throughout day
        - 20% clustered around quarter-hours
        """
        total_seconds = duration_hours * 3600
        distribution = []

        peak_messages = int(count * 0.3)
        distributed_messages = int(count * 0.5)
        clustered_messages = count - peak_messages - distributed_messages

        # Peak hours: tighter spacing
        # Clamp peak hours to campaign duration
        peak_windows = [
            (10 * 3600, 11 * 3600),  # 10-11am in seconds
            (14 * 3600, 15 * 3600),  # 2-3pm in seconds
        ]
        for _ in range(peak_messages):
            window = self.rng.choice(len(peak_windows))
            start, end = peak_windows[window]
            # Clamp to campaign duration
            start = min(start, total_seconds)
            end = min(end, total_seconds)
            if start < end:
                time_in_window = self.rng.uniform(start, end)
            else:
                # If peak window is outside duration, use random time in duration
                time_in_window = self.rng.uniform(0, total_seconds)
            distribution.append(time_in_window)

        # Distributed: sparse across day
        for _ in range(distributed_messages):
            time_in_day = self.rng.uniform(0, total_seconds)
            distribution.append(time_in_day)

        # Clustered: around quarter-hours
        quarter_minutes = [0, 15, 30, 45]
        for _ in range(clustered_messages):
            hour = self.rng.randint(9, 17)  # Business hours
            quarter = self.rng.choice(quarter_minutes)
            base_time = hour * 3600 + quarter * 60
            # Add jitter around quarter-hour (+/-3 minutes)
            jitter = self.rng.normal(0, 180)
            cluster_time = base_time + jitter
            cluster_time = np.clip(cluster_time, 0, total_seconds)
            distribution.append(cluster_time)

        # Sort and return
        distribution.sort()
        return distribution
