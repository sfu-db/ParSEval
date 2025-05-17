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
                      "index": 3,
                      "name": "$3",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "paper",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "LIKE",
                  "operator": "LIKE",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 19,
                      "name": "$19",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "+%",
                      "type": "CHAR",
                      "nullable": false,
                      "precision": 2
                    }
                  ]
                },
                {
                  "kind": "NOT_EQUALS",
                  "operator": "<>",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 19,
                      "name": "$19",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "+0",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
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