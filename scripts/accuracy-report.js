/**
 * Accuracy Report — Feedback Aggregation Pipeline
 *
 * Run: mongosh "$MONGODB_URI" scripts/accuracy-report.js
 *
 * Reports satisfaction/accuracy by language + model provider
 * over the last 7 days (configurable via DAYS env var).
 */

const db = db.getSiblingDB('syrabit_prod');

const DAYS = parseInt(process.env.DAYS || "7", 10);
const since = new Date(Date.now() - DAYS * 24 * 60 * 60 * 1000);

print("");
print("═══════════════════════════════════════════════════════");
print("  SYRABIT — Accuracy Report (Last " + DAYS + " Days)");
print("═══════════════════════════════════════════════════════");
print("");

const results = db.chat_feedback.aggregate([
  { $match: { timestamp: { $gte: since } } },
  {
    $group: {
      _id: { lang: "$lang", model: "$model_provider" },
      total: { $sum: 1 },
      accurate: { $sum: { $cond: [{ $eq: ["$rating", 1] }, 1, 0] } },
      inaccurate: { $sum: { $cond: [{ $eq: ["$rating", -1] }, 1, 0] } },
      avg_latency: { $avg: "$latency_ms" },
    },
  },
  {
    $addFields: {
      accuracy: { $round: [{ $divide: ["$accurate", "$total"] }, 3] },
      satisfaction_pct: {
        $round: [{ $multiply: [{ $divide: ["$accurate", "$total"] }, 100] }, 1],
      },
    },
  },
  { $sort: { "_id.lang": 1, accuracy: -1 } },
]).toArray();

if (results.length === 0) {
  print("  No feedback data found in the last " + DAYS + " days.");
  print("  Make sure users are submitting ratings via the chat UI.");
} else {
  print(
    "  " +
      "LANG".padEnd(6) +
      "MODEL".padEnd(18) +
      "SATISFACTION".padEnd(14) +
      "TOTAL".padEnd(8) +
      "+".padEnd(5) +
      "-".padEnd(5) +
      "AVG LATENCY"
  );
  print("  " + "─".repeat(70));

  results.forEach(function (r) {
    const latency = r.avg_latency ? Math.round(r.avg_latency) + "ms" : "N/A";
    print(
      "  " +
        r._id.lang.toUpperCase().padEnd(6) +
        r._id.model.padEnd(18) +
        (r.satisfaction_pct + "%").padEnd(14) +
        String(r.total).padEnd(8) +
        String(r.accurate).padEnd(5) +
        String(r.inaccurate).padEnd(5) +
        latency
    );
  });
}

print("");

// ── Summary stats ──
const totalFeedback = db.chat_feedback.countDocuments({ timestamp: { $gte: since } });
const totalPositive = db.chat_feedback.countDocuments({ timestamp: { $gte: since }, rating: 1 });
const overallRate = totalFeedback > 0 ? ((totalPositive / totalFeedback) * 100).toFixed(1) : "N/A";

print("  ── Summary ──");
print("  Total ratings:        " + totalFeedback);
print("  Overall satisfaction:  " + overallRate + "%");
print("  Period:                Last " + DAYS + " days");
print("");
print("═══════════════════════════════════════════════════════");
print("");
