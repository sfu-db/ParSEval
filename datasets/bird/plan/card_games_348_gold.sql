{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "INTEGER"
    },
    {
      "kind": "INPUT_REF",
      "index": 76,
      "name": "$76",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 21,
      "name": "$21",
      "type": "INTEGER"
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
            "value": "Stephen Daniele",
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
                "index": 71,
                "name": "$71",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 77,
                "name": "$77",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "cards",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "rulings",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}