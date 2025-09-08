{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "8",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "VARCHAR"
        }
      ],
      "id": "7",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 12,
                "name": "$12",
                "type": "INTEGER"
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
                        "operator": "MAX",
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
                            "index": 5,
                            "name": "$5",
                            "type": "INTEGER"
                          }
                        ],
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "Player_Attributes",
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
          },
          "variableset": "[]",
          "id": "6",
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
                    "index": 1,
                    "name": "$1",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 9,
                    "name": "$9",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "5",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Player",
                  "id": "3",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Player_Attributes",
                  "id": "4",
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