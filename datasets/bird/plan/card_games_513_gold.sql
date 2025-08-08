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
  "id": "3",
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
              "kind": "OR",
              "operator": "OR",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "LIKE",
                  "operator": "LIKE",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 54,
                      "name": "$54",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "%*%",
                      "type": "CHAR",
                      "nullable": false,
                      "precision": 3
                    }
                  ]
                },
                {
                  "kind": "IS_NULL",
                  "operator": "IS NULL",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 54,
                      "name": "$54",
                      "type": "VARCHAR"
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
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
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
                    "index": 22,
                    "name": "$22",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 0,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 11,
                    "name": "$11",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "a",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
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
  ]
}