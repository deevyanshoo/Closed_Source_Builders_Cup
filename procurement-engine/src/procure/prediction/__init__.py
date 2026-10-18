"""Price outlook built on the existing forecast engine (procure/forecast), applied to dated public monthly series.

procure/forecast/simulate.py and select.py are bound to the planning database, settings and a commodity-times-USD/INR
pair, so this package calls the lower-level engine pieces (models.fit_series, select.metrics_from) for one series at
a time. Nothing under procure/forecast or procure/evaluation is modified.
"""
