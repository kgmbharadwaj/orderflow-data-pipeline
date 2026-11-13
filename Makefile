PY ?= python3

test:        ## run the automated test suite
	$(PY) -m unittest discover -s tests -t . -v

sample:      ## load the committed 300-row sample, reconcile, and print reports
	rm -f demo.db*
	$(PY) -m pipeline load --db demo.db --customers data/sample/customers.csv --products data/sample/products.csv --orders data/sample/orders.csv
	$(PY) -m pipeline reconcile --db demo.db --orders data/sample/orders.csv --expected data/sample/expected_summary.json --labels data/sample/orders_labels.csv
	$(PY) -m pipeline report --db demo.db

large:       ## generate + load 100k rows (change ROWS=...) and reconcile
	$(PY) -m pipeline generate --out data/generated --rows $(or $(ROWS),100000) --seed 42 > /dev/null
	rm -f large.db*
	$(PY) -m pipeline load --db large.db --customers data/generated/customers.csv --products data/generated/products.csv --orders data/generated/orders.csv
	$(PY) -m pipeline reconcile --db large.db --orders data/generated/orders.csv --expected data/generated/expected_summary.json --labels data/generated/orders_labels.csv

bench:       ## throughput + peak memory
	$(PY) -m pipeline benchmark --rows $(or $(ROWS),100000)
