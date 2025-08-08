{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 3,
  "id": "3",
  "inputs": [
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
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "IS_NULL",
            "operator": "IS NULL",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 14,
                "name": "$14",
                "type": "VARCHAR"
              }
            ]
          },
          "variableset": "[]",
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "sets",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}