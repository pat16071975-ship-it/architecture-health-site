# Paid economics in the two approved views

Source/tests only. No merge, deployment, database writes, source re-import, permissions changes, or advertising changes.

Both existing views now select an exact payer period and calculate direction average checks from the same MIS revenue that is displayed. No mixture of a newer payer count with an older paid snapshot. Paid-only exact dates can display payer/direction values without fabricated cash or clinical rows.

The common expense numerator is divided by all medical payers; a scope receives its own payer count multiplied by that common unit cost. A complete monthly expense numerator is not divided by a partial-month payer count. Missing expenses and zero overall payers do not fabricate zero cost or profit. Ambiguous surname-only doctor matches are not guessed.

IMPORTANT OPEN PERIOD DECISION: the historical monthly uploads prove final monthly counts, not counts on day27 of every month. The existing same-day comparison is preserved. Tests explicitly require day27 historical gaps to remain unknown. On day31, existing calendar clamping exposes the nine closed months; October31 remains unknown when the source ends October27. This is not completion of the request to display all ten months together on day27, and it is not permission to change comparison semantics.

All test data are synthetic. Medicine average continues to use factMedicine; cash Fact/OOO/IP, primary/repeat, laboratory formula, original sources, upload logic, and backend are unchanged.

CARC last-row advertising request and advertising date controls are deferred until management is completed. No CARC formula is installed in this change.
