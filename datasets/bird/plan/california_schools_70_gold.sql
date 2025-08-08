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
                "index": 27,
                "name": "$27",
                "type": "VARCHAR"
              }
            ]
          },
          {
            "kind": "LITERAL",
            "value": 31,
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
          "table": "schools",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}