{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 15,
      "name": "$15",
      "type": "VARCHAR"
    }
  ],
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "GREATER_THAN",
        "operator": ">",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "TIMES",
            "operator": "*",
            "type": "INTEGER",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 10,
                "name": "$10",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 100,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          {
            "kind": "TIMES",
            "operator": "*",
            "type": "INTEGER",
            "operands": [
              {
                "kind": "SCALAR_QUERY",
                "operator": "$SCALAR_QUERY",
                "operands": [],
                "query": [
                  {
                    "relOp": "LogicalAggregate",
                    "keys": [],
                    "aggs": [
                      {
                        "operator": "AVG",
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
                            "kind": "INPUT_REF",
                            "index": 10,
                            "name": "$10",
                            "type": "INTEGER"
                          }
                        ],
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "superhero",
                            "id": "0",
                            "inputs": []
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 80,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "8",
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
                "index": 13,
                "name": "$13",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 14,
                "name": "$14",
                "type": "INTEGER"
              }
            ]
          },
          "id": "7",
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
                    "index": 12,
                    "name": "$12",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "5",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "superhero",
                  "id": "3",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "hero_power",
                  "id": "4",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "superpower",
              "id": "6",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}