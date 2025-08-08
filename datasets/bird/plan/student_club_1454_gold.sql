{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "VARCHAR"
    }
  ],
  "id": "9",
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
                "index": 8,
                "name": "$8",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Parking",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "LESS_THAN",
            "operator": "<",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 17,
                "name": "$17",
                "type": "FLOAT"
              },
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
                            "type": "FLOAT"
                          }
                        ],
                        "type": "FLOAT",
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
                            "index": 3,
                            "name": "$3",
                            "type": "FLOAT"
                          }
                        ],
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "expense",
                            "id": "0",
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
                "index": 7,
                "name": "$7",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 20,
                "name": "$20",
                "type": "VARCHAR"
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
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 13,
                    "name": "$13",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "5",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "event",
                  "id": "3",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "budget",
                  "id": "4",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "expense",
              "id": "6",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}