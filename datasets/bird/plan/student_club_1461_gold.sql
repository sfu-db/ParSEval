{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 8,
      "name": "$8",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 9,
      "name": "$9",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 3,
      "name": "$3",
      "type": "FLOAT"
    }
  ],
  "id": "4",
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
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "Water, Veggie tray, supplies",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 5,
                "name": "$5",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 7,
                "name": "$7",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "expense",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "member",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}