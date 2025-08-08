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
            "index": 1,
            "name": "$1",
            "type": "INTEGER"
          },
          {
            "kind": "LITERAL",
            "value": 2009,
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
          "table": "races",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}