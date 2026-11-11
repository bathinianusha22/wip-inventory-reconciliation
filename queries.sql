-- ============================================================================
-- Tesla WIP Inventory Reconciliation - Useful Queries
-- Run against inventory.db, e.g.:  sqlite3 inventory.db < queries.sql
-- All data is synthetic.
-- ============================================================================

-- 1. Total variance value: the total dollar exposure of all discrepancies.
SELECT
    COUNT(*) AS discrepancy_count,
    ROUND(SUM(ABS(variance_value)), 2) AS total_abs_variance_value_usd,
    ROUND(SUM(variance_value), 2) AS net_variance_value_usd
FROM discrepancies;

-- 2. Top 10 discrepancies by absolute value (where to focus first).
SELECT
    d.part_number,
    m.description,
    d.location,
    d.variance_type,
    d.variance_qty,
    ROUND(ABS(d.variance_value), 2) AS abs_variance_value_usd,
    d.aging_bucket
FROM discrepancies d
JOIN mes_inventory m ON m.record_id = d.record_id
ORDER BY ABS(d.variance_value) DESC
LIMIT 10;

-- 3. Aging over 30 days: stale discrepancies that have sat unresolved.
SELECT
    aging_bucket,
    COUNT(*) AS discrepancy_count,
    ROUND(SUM(ABS(variance_value)), 2) AS abs_variance_value_usd
FROM discrepancies
WHERE aging_days > 30
GROUP BY aging_bucket
ORDER BY MIN(aging_days);

-- 4. Disposition summary by status.
SELECT
    status,
    COUNT(*) AS disposition_count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM dispositions), 1)
        AS pct_of_dispositions
FROM dispositions
GROUP BY status
ORDER BY status;

-- 5. Variance by location: which areas / warehouses drive the exposure.
SELECT
    location,
    COUNT(*) AS discrepancy_count,
    ROUND(SUM(ABS(variance_value)), 2) AS abs_variance_value_usd,
    ROUND(100.0 * SUM(ABS(variance_value))
        / (SELECT SUM(ABS(variance_value)) FROM discrepancies), 1)
        AS pct_of_total_variance
FROM discrepancies
GROUP BY location
ORDER BY abs_variance_value_usd DESC;

-- 6. ABC classification: how annual usage value is distributed across classes.
SELECT
    abc_class,
    COUNT(*) AS part_count,
    ROUND(SUM(annual_usage_value), 2) AS total_annual_value_usd,
    ROUND(100.0 * SUM(annual_usage_value)
        / (SELECT SUM(annual_usage_value) FROM abc_classification), 1)
        AS pct_of_annual_value
FROM abc_classification
GROUP BY abc_class
ORDER BY abc_class;

-- 7. Open items past due: open / in-progress dispositions past their due date.
SELECT
    dp.disposition_id,
    d.part_number,
    d.location,
    d.variance_type,
    ROUND(ABS(d.variance_value), 2) AS abs_variance_value_usd,
    dp.disposition,
    dp.owner,
    dp.due_date,
    dp.status
FROM dispositions dp
JOIN discrepancies d ON d.discrepancy_id = dp.discrepancy_id
WHERE dp.status IN ('OPEN', 'IN-PROGRESS')
  AND dp.due_date < date('2026-11-11')
ORDER BY dp.due_date ASC;

-- 8. Count accuracy by ABC class: share of records with zero variance.
SELECT
    a.abc_class,
    COUNT(*) AS records_counted,
    SUM(CASE WHEN d.discrepancy_id IS NULL THEN 1 ELSE 0 END)
        AS records_with_no_variance,
    ROUND(100.0 * SUM(CASE WHEN d.discrepancy_id IS NULL THEN 1 ELSE 0 END)
        / COUNT(*), 1) AS count_accuracy_pct
FROM mes_inventory m
JOIN abc_classification a ON a.part_number = m.part_number
LEFT JOIN discrepancies d ON d.record_id = m.record_id
GROUP BY a.abc_class
ORDER BY a.abc_class;

-- 9. Disposition type mix: what is happening to the unaccounted-for material.
SELECT
    disposition,
    COUNT(*) AS disposition_count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM dispositions), 1)
        AS pct_of_dispositions
FROM dispositions
GROUP BY disposition
ORDER BY disposition_count DESC;

-- 10. Variance by part category.
SELECT
    m.category,
    COUNT(*) AS discrepancy_count,
    ROUND(SUM(ABS(d.variance_value)), 2) AS abs_variance_value_usd
FROM discrepancies d
JOIN mes_inventory m ON m.record_id = d.record_id
GROUP BY m.category
ORDER BY abs_variance_value_usd DESC;
