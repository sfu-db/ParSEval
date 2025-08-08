{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 4,
      "name": "$4",
      "type": "DATE"
    },
    {
      "kind": "INPUT_REF",
      "index": 6,
      "name": "$6",
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
            "index": 0,
            "name": "$0",
            "type": "INTEGER"
          },
          {
            "kind": "LITERAL",
            "value": 48473,
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
          "table": "Patient",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}