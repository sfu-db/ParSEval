{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 40,
      "name": "$40",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 5,
      "name": "$5",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 4,
      "name": "$4",
      "type": "VARCHAR"
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
            "index": 23,
            "name": "$23",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "0040",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
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