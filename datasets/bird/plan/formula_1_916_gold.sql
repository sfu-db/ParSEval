{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "DATE"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 7,
          "name": "$7",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 6,
          "name": "$6",
          "type": "DATE"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
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
                    "type": "DATE"
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
              "table": "drivers",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}