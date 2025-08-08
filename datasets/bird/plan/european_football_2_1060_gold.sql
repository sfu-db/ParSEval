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
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "GREATER_THAN",
        "operator": ">",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 5,
            "name": "$5",
            "type": "INTEGER"
          },
          {
            "kind": "LITERAL",
            "value": 180,
            "type": "INTEGER",
            "nullable": false,
            "precision": 10
          }
        ]
      },
      "variableset": "[]",
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "Player",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}