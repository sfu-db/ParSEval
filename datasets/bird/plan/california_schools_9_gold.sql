{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 6,
      "name": "$6",
      "type": "INTEGER"
    }
  ],
  "id": "6",
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
            "index": 0,
            "name": "$0",
            "type": "VARCHAR"
          },
          {
            "kind": "SCALAR_QUERY",
            "operator": "$SCALAR_QUERY",
            "operands": [],
            "query": [
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
                "id": "3",
                "inputs": [
                  {
                    "relOp": "LogicalSort",
                    "sort": [
                      {
                        "column": 1,
                        "type": "FLOAT"
                      }
                    ],
                    "dir": [
                      "DESCENDING"
                    ],
                    "offset": 0,
                    "limit": 1,
                    "id": "2",
                    "inputs": [
                      {
                        "relOp": "LogicalProject",
                        "project": [
                          {
                            "kind": "INPUT_REF",
                            "index": 0,
                            "name": "$0",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 21,
                            "name": "$21",
                            "type": "FLOAT"
                          }
                        ],
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "frpm",
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
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "satscores",
          "id": "4",
          "inputs": []
        }
      ]
    }
  ]
}