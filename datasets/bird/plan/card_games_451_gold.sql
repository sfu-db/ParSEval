{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "SUM",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "INTEGER"
        }
      ],
      "type": "INTEGER",
      "name": "EXPR$0"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "CASE",
          "operator": "CASE",
          "type": "INTEGER",
          "operands": [
            {
              "kind": "AND",
              "operator": "AND",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Aaron Miller",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "NOT",
                  "operator": "NOT",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "IS_NULL",
                      "operator": "IS NULL",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 5,
                          "name": "$5",
                          "type": "VARCHAR"
                        }
                      ]
                    }
                  ]
                },
                {
                  "kind": "NOT",
                  "operator": "NOT",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "IS_NULL",
                      "operator": "IS NULL",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 6,
                          "name": "$6",
                          "type": "VARCHAR"
                        }
                      ]
                    }
                  ]
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": 1,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            },
            {
              "kind": "LITERAL",
              "value": 0,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        }
      ],
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "cards",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}