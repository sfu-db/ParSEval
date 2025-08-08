{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": true,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "INTEGER"
        }
      ],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "INTEGER"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "OR",
            "operator": "OR",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 53,
                    "name": "$53",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "negative",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
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
                        "index": 53,
                        "name": "$53",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "0",
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
                            "index": 11,
                            "name": "$11",
                            "type": "VARCHAR"
                          }
                        ]
                      }
                    ]
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 0,
                    "name": "$0",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 13,
                    "name": "$13",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Examination",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Laboratory",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}