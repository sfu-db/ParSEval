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
  "id": "5",
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
            "index": 2,
            "name": "$2",
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
                    "operator": "MIN",
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
                        "index": 2,
                        "name": "$2",
                        "type": "INTEGER"
                      }
                    ],
                    "id": "1",
                    "inputs": [
                      {
                        "relOp": "LogicalTableScan",
                        "table": "hero_attribute",
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
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "hero_attribute",
          "id": "3",
          "inputs": []
        }
      ]
    }
  ]
}