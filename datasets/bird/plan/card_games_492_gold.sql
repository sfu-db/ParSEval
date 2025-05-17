{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 13,
      "name": "$13",
      "type": "VARCHAR"
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
            "kind": "INPUT_REF",
            "index": 17,
            "name": "$17",
            "type": "DATE"
          },
          {
            "kind": "CAST",
            "operator": "CAST",
            "type": "DATE",
            "operands": [
              {
                "kind": "LITERAL",
                "value": "2017-06-09",
                "type": "CHAR",
                "nullable": false,
                "precision": 10
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
          "table": "sets",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}