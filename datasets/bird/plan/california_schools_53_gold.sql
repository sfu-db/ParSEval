{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
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
          "type": "VARCHAR"
        }
      ],
      "id": "4",
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
                    "index": 25,
                    "name": "$25",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Lakeport",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              {
                "kind": "GREATER_THAN_OR_EQUAL",
                "operator": ">=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "PLUS",
                    "operator": "+",
                    "type": "INTEGER",
                    "operands": [
                      {
                        "kind": "PLUS",
                        "operator": "+",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 7,
                            "name": "$7",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 8,
                            "name": "$8",
                            "type": "INTEGER"
                          }
                        ]
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 9,
                        "name": "$9",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": 1500,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
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
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 11,
                    "name": "$11",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "satscores",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "schools",
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