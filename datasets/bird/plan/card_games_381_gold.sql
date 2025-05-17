{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 12,
      "name": "$12",
      "type": "INTEGER"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "EQUALS",
        "operator": "=",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "CAST",
            "operator": "CAST",
            "type": "INTEGER",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 18,
                "name": "$18",
                "type": "VARCHAR"
              }
            ]
          },
          {
            "kind": "LITERAL",
            "value": 2015,
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
          "table": "cards",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}